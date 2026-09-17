"""Pipeline orchestrator -- wires Stages 1-5 into a single retrieve/search flow.

The orchestrator is the entry point for ``POST /retrieve`` and
``POST /search``.  It calls each stage function in sequence with hard
gates: a BLOCKED verdict at Stage 2 or an INJECTION_DETECTED verdict at
Stage 3 halts the pipeline and returns a quarantine
:class:`RetrievedContent`.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import unicodedata
import uuid
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol
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
from pipeline import contract
from pipeline.extraction_limits import (
    MAX_EXTRACTED_OUTPUT_BYTES,
    MAX_PROMPTGUARD_CHUNKS,
    ExtractionSettings,
    max_extracted_characters,
)
from pipeline.pdf_subprocess import (
    PDFClassifiableTextLimitError,
    extract_pdf_in_subprocess,
)
from pipeline.search_providers.base import (
    ProviderFailure,
    ProviderSearchResult,
    SearchProvider,
)
from pipeline.search_providers.searxng import (
    DEFAULT_SEARXNG_URL,
    HTTP_STATUS_DETAIL_PREFIX,
    SEARXNG_ENGINES,
    SEARXNG_PROVIDER_NAME,
    SearxngProvider,
)
from pipeline.stage1_extraction import ExtractionResult, extract_html
from pipeline.stage1_pdf import (
    PDFEncryptedError,
    PDFExtractionError,
    PDFNoTextError,
    PDFTooLargeError,
    detect_content_type,
    extract_pdf,
)
from pipeline.stage1_upload import (
    UnsupportedUploadFormatError,
    UploadTextClassifiableLimitError,
    detect_upload_content_type,
    extract_upload_text,
    extract_upload_text_file,
)
from pipeline.stage2_structural import scan_structural
from pipeline.stage3_promptguard import PromptGuardResult, run_promptguard
from pipeline.stage4_structuring import (
    SanitizationResult,
    build_extracted_content,
    build_retrieved_content,
    structure_sanitization_result,
)
from pipeline.stage5_url_audit import ContentTooLargeError, fetch_url
from promptguard.classifier import PromptGuardBudgetExceededError
from url_validator import BlockedDomainError, PrivateIPError, validate_url

if TYPE_CHECKING:
    from promptguard.classifier import PromptGuardClassifier

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Error helpers
# ---------------------------------------------------------------------------

DOCUMENT_FAILURE_CODES = frozenset(
    {
        "content_too_large",
        "content_too_large_to_classify",
        "pdf_encrypted",
        "pdf_no_text",
        "unsupported_format",
        "extraction_failed",
        "busy",
    }
)

DOCUMENT_FAILURE_REASONS = {
    "content_too_large": (
        "Document exceeds the 50 MB extraction limit. Documents between 50 MB "
        "and the core upload limit of 100 MB can be uploaded but cannot be extracted."
    ),
    "content_too_large_to_classify": ("Document text is too large to classify safely."),
    "pdf_encrypted": "PDF is encrypted and cannot be extracted.",
    "pdf_no_text": "PDF contains no extractable text. OCR is not supported.",
    "unsupported_format": "Document format is unsupported or its content is invalid.",
    "extraction_failed": "Document extraction failed.",
    "busy": "Document extraction is currently busy. Please try again.",
}


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


def document_failure(error: str, request_id: str) -> PipelineError:
    """Build a content-free, taxonomy-backed document extraction failure."""
    if error not in DOCUMENT_FAILURE_CODES:
        raise ValueError(f"Unsupported document failure code: {error}")
    return PipelineError(
        error=error,
        reason=DOCUMENT_FAILURE_REASONS[error],
        request_id=request_id,
    )


async def sanitize_and_structure(
    *,
    extraction: ExtractionResult,
    trust_tier: TrustTier,
    classifier: PromptGuardClassifier | None,
    promptguard_threshold: float,
    promptguard_fail_closed: bool,
    extract_mode: str,
    content_type: str,
    sanitizer_revision: str = "",
    domain_changed_on_redirect: bool = False,
    max_promptguard_chunks: int | None = None,
) -> SanitizationResult:
    """Run the shared Stage 2-4 gauntlet for any extracted content source."""
    structural = scan_structural(extraction.raw_text)
    promptguard = PromptGuardResult(
        verdict=Stage3Verdict.SAFE,
        score=0.0,
        skipped=True,
        skip_reason="structural_block",
    )
    if structural.verdict != Stage2Verdict.BLOCKED:
        promptguard = await run_promptguard(
            extraction.raw_text,
            classifier,
            threshold=promptguard_threshold,
            trust_tier=trust_tier,
            fail_closed=promptguard_fail_closed,
            max_chunks=max_promptguard_chunks,
        )

    return structure_sanitization_result(
        extraction=extraction,
        structural=structural,
        promptguard=promptguard,
        trust_tier=trust_tier,
        extract_mode=extract_mode,
        content_type=content_type,
        sanitizer_revision=sanitizer_revision,
        domain_changed_on_redirect=domain_changed_on_redirect,
    )


# ---------------------------------------------------------------------------
# Retrieve pipeline
# ---------------------------------------------------------------------------


async def run_retrieve_pipeline(
    request: RetrieveRequest,
    *,
    cache: ContentCache | None,
    classifier: PromptGuardClassifier | None,
    config: dict[str, Any],
    sanitizer_revision: str,
) -> RetrievedContent:
    """Run the full 5-stage retrieval pipeline.

    Parameters
    ----------
    request:
        Inbound retrieval request with URL and options.
    cache:
        Content cache over the selected storage (may be ``None`` if the
        service has none).
    classifier:
        PromptGuard 2 classifier instance (may be ``None``).
    config:
        Sidecar configuration loaded from ``config.yaml``.
    sanitizer_revision:
        The revision this process sanitizes under, derived once at startup and
        passed in the way ``run_extract_pipeline`` already takes it. It keys
        the cache: content sanitized under an older pipeline must not be
        replayed by a newer one.

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
        classifier_loaded=classifier is not None and classifier.loaded,
        sanitizer_revision=sanitizer_revision,
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

    # Determine domain from final URL
    parsed_final = urlparse(fetch_result.final_url)
    domain = parsed_final.hostname or ""
    trust_tier = TrustTier(
        _resolve_request_trust_tier(
            domain,
            request.trusted_domains,
            request.verified_domains,
            blocked_domains,
        )
    )
    sanitization = await sanitize_and_structure(
        extraction=extraction,
        trust_tier=trust_tier,
        classifier=classifier,
        promptguard_threshold=request.promptguard_threshold,
        promptguard_fail_closed=request.promptguard_fail_closed,
        extract_mode=request.extract_mode,
        content_type=content_type,
        domain_changed_on_redirect=fetch_result.domain_changed_on_redirect,
    )
    if sanitization.injection_detected:
        logger.warning(
            "Content quarantined for %s — returning content-free response",
            request.url,
        )
    content = build_retrieved_content(
        request_id=request_id,
        source_url=request.url,
        final_url=fetch_result.final_url,
        domain=domain,
        sanitization=sanitization,
        redirect_chain=fetch_result.redirect_chain,
        domain_changed_on_redirect=fetch_result.domain_changed_on_redirect,
    )

    # -- Step 8: Cache safe result --
    if (
        cache is not None
        and request.cache_ttl_hours > 0
        and not content.injection_detected
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
        raise document_failure("unsupported_format", request_id) from exc
    except PDFTooLargeError as exc:
        raise document_failure("content_too_large", request_id) from exc
    except PDFEncryptedError as exc:
        raise document_failure("pdf_encrypted", request_id) from exc
    except PDFNoTextError as exc:
        raise document_failure("pdf_no_text", request_id) from exc
    except PDFExtractionError as exc:
        raise document_failure("extraction_failed", request_id) from exc
    except Exception as exc:
        raise document_failure("extraction_failed", request_id) from exc

    if (
        len(extraction.raw_text) > max_extracted_characters(MAX_PROMPTGUARD_CHUNKS)
        or len(extraction.raw_text.encode("utf-8")) > MAX_EXTRACTED_OUTPUT_BYTES
    ):
        raise document_failure("content_too_large_to_classify", request_id)
    try:
        sanitization = await sanitize_and_structure(
            extraction=extraction,
            trust_tier=TrustTier.UNTRUSTED,
            classifier=classifier,
            promptguard_threshold=promptguard_threshold,
            promptguard_fail_closed=True,
            extract_mode=extract_mode,
            content_type=content_type,
            sanitizer_revision=sanitizer_revision,
            max_promptguard_chunks=MAX_PROMPTGUARD_CHUNKS,
        )
    except PromptGuardBudgetExceededError as exc:
        raise document_failure("content_too_large_to_classify", request_id) from exc

    return build_extracted_content(
        request_id=request_id,
        provenance=UploadProvenance(filename=filename, mime_hint=mime_hint),
        sanitization=sanitization,
    )


async def run_extract_pipeline_from_file(
    path: Path,
    *,
    filename: str,
    mime_hint: str | None,
    extract_mode: str,
    request_id: str,
    classifier: PromptGuardClassifier | None,
    promptguard_threshold: float,
    sanitizer_revision: str,
    settings: ExtractionSettings,
    classification_semaphore: asyncio.Semaphore,
) -> ExtractedContent:
    """Extract a spooled upload with bounded PDF parsing and classification."""
    try:
        with path.open("rb") as source:
            magic_bytes = source.read(5)
        if magic_bytes == b"%PDF-":
            extraction = await asyncio.to_thread(
                extract_pdf_in_subprocess,
                path,
                settings,
            )
            content_type = "pdf"
        else:
            extraction = extract_upload_text_file(
                path,
                max_characters=settings.max_extracted_characters,
                max_output_bytes=settings.max_extracted_output_bytes,
            )
            content_type = "text"
    except UploadTextClassifiableLimitError as exc:
        raise document_failure("content_too_large_to_classify", request_id) from exc
    except PDFClassifiableTextLimitError as exc:
        raise document_failure("content_too_large_to_classify", request_id) from exc
    except UnsupportedUploadFormatError as exc:
        raise document_failure("unsupported_format", request_id) from exc
    except PDFEncryptedError as exc:
        raise document_failure("pdf_encrypted", request_id) from exc
    except PDFNoTextError as exc:
        raise document_failure("pdf_no_text", request_id) from exc
    except PDFExtractionError as exc:
        raise document_failure("extraction_failed", request_id) from exc
    except OSError as exc:
        raise document_failure("extraction_failed", request_id) from exc

    if (
        len(extraction.raw_text) > settings.max_extracted_characters
        or len(extraction.raw_text.encode("utf-8"))
        > settings.max_extracted_output_bytes
    ):
        raise document_failure("content_too_large_to_classify", request_id)

    try:
        async with classification_semaphore:
            sanitization = await sanitize_and_structure(
                extraction=extraction,
                trust_tier=TrustTier.UNTRUSTED,
                classifier=classifier,
                promptguard_threshold=promptguard_threshold,
                promptguard_fail_closed=True,
                extract_mode=extract_mode,
                content_type=content_type,
                sanitizer_revision=sanitizer_revision,
                max_promptguard_chunks=settings.max_promptguard_chunks,
            )
    except PromptGuardBudgetExceededError as exc:
        raise document_failure("content_too_large_to_classify", request_id) from exc

    return build_extracted_content(
        request_id=request_id,
        provenance=UploadProvenance(filename=filename, mime_hint=mime_hint),
        sanitization=sanitization,
    )


# ---------------------------------------------------------------------------
# Search pipeline
# ---------------------------------------------------------------------------

# Both constants are *defined* in `pipeline/search_providers/searxng.py`,
# which owns the SearXNG call now; these are assigned aliases, not second
# copies. They stay because three test modules import the private names from
# here — `tests/test_searxng_docker.py` (the `searxng/config/settings.yml`
# sync guard), `tests/test_compose_fragments.py` and
# `tests/test_searxng_smoke.py`. Assigned rather than imported: importing a
# private name across modules fails pyright strict's `reportPrivateUsage`,
# whose only carve-out is `tests/`.
_DEFAULT_SEARXNG_URL = DEFAULT_SEARXNG_URL
_SEARXNG_ENGINES = SEARXNG_ENGINES
_MAX_SEARCH_RESULTS_SCANNED = 20

# `unresponsive_engines` crosses the provider seam as a list of strings the
# backend chose, so it is bounded here — in hashed orchestrator code — rather
# than in the provider, which normalizes nothing (contract point 3). The
# honest response names four engines, so no real body is touched.
_MAX_UNRESPONSIVE_ENGINES = 16
_MAX_UNRESPONSIVE_ENGINE_LENGTH = 64
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


def _canonicalize_search_url(value: object) -> tuple[str, str, str] | None:
    """Normalize a result URL and allow only canonical HTTP(S) URLs.

    Returns ``(canonical_url, scanned_text, domain)``. ``domain`` is bound
    from ``parsed.hostname`` before the IPv6 re-bracketing below, so an IPv6
    literal reaches the wire unbracketed (``2001:db8::1``) even though
    ``canonical_url`` carries the bracketed form (``[2001:db8::1]``) — the one
    case where ``domain`` is not a substring of ``canonical_url``.
    """
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

    domain = parsed.hostname.lower()
    host = domain
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
        domain,
    )


def _search_result_promptguard_input(title: str, url: str, snippet: str) -> str:
    """Build the single bounded PromptGuard input for a complete result."""
    return f"Title: {title}\nURL: {url}\nSnippet: {snippet}"


def _legacy_searxng_codes(chain: Sequence[SearchProvider]) -> bool:
    """Whether *chain* is the one configuration the ``searxng_*`` codes describe.

    The legacy pair predates the provider seam, when SearXNG was the only
    backend there was, and their wire text names SearXNG explicitly. They
    therefore stay bound to exactly that deployment: a chain of one provider
    whose ``name`` is ``"searxng"``. Every other chain — including one that
    merely *starts* with SearXNG — refuses with ``search_unavailable``, whose
    reason names whichever provider actually failed.

    The test is a comparison on the chain's ``name`` token and never an
    ``isinstance`` (ruling 28): ``name`` is the single identifier a provider
    carries, the one the operator wrote in ``FORAGE_SEARCH_PROVIDERS``, and a
    class check would make a drop-in replacement for ``SearxngProvider``
    silently change the wire code an operator's dashboards are keyed on.

    It reads the **configured** chain, never the per-request effective one:
    ``run_search_pipeline`` feeds it *configured_chain* when the caller
    supplies one, else *providers* — spec 4's per-request policy filtering
    narrows *providers* but must never reach this predicate (ruling 28), so
    no status code varies with a policy parameter. What a consumer is told
    depends on how the deployment was set up, which is stable across
    requests, rather than on which backend happened to be reached.
    """
    return len(chain) == 1 and chain[0].name == SEARXNG_PROVIDER_NAME


def _searxng_pipeline_error(
    provider: SearchProvider,
    failure: ProviderFailure,
    *,
    request_id: str,
) -> PipelineError:
    """Map a SearXNG ``ProviderFailure`` onto the two legacy `/search` codes.

    The codes and the 422 are unchanged from the inline call; only the
    ``reason`` text narrowed. ``str(exc)`` is gone — ruling 13 keeps
    exception text behind the seam — and the endpoint echo is
    ``provider.origin``, the userinfo-stripped scheme, host and port, so a
    credential in ``SEARXNG_URL`` cannot reach a 422 body on an
    unauthenticated route. Host and port still appear, which is what makes a
    misconfigured deployment diagnosable from the response alone.
    """
    if failure.detail.startswith(HTTP_STATUS_DETAIL_PREFIX):
        return PipelineError(
            error="searxng_error",
            reason=f"SearXNG returned HTTP error ({failure.detail})",
            request_id=request_id,
        )
    return PipelineError(
        error="searxng_unavailable",
        reason=f"SearXNG not reachable at {provider.origin}: {failure.detail}",
        request_id=request_id,
    )


def _search_unavailable_error(
    provider_errors: list[str],
    *,
    request_id: str,
) -> PipelineError:
    """Map a non-legacy chain's exhaustion onto ``search_unavailable``.

    *provider_errors* is the chain-order list of ``"<provider.name>:
    <failure_class>"`` entries built while traversing — one per provider
    tried, each composed from two closed vocabularies and nothing else, so no
    endpoint, credential, header or exception text can reach a 422 body
    through this path, whatever a third-party API put in its response. The
    reason is those entries joined by ``"; "``.
    """
    return PipelineError(
        error="search_unavailable",
        reason="; ".join(provider_errors),
        request_id=request_id,
    )


class SearchMetricsSink(Protocol):
    """The two ``/metrics`` search counters ``run_search_pipeline`` increments directly.

    ``retrieval_app.SearchMetrics`` satisfies this structurally — neither
    module imports the other. Declaring it here, on the consumer side, is the
    same seam shape as :class:`SearchProvider`.
    """

    fallback_fired: int
    paid_calls: int


class _NullSearchMetrics:
    """The ``search_metrics`` parameter's default — a real counter nobody reads.

    Its counters are process-local scratch space, satisfying
    :class:`SearchMetricsSink` structurally so every increment site below is
    unconditional (the ``metrics if metrics is not None else CacheMetrics()``
    idiom, ``cache.py``), with no ``is not None`` branch at the increment
    site itself.
    """

    def __init__(self) -> None:
        self.fallback_fired = 0
        self.paid_calls = 0


async def run_search_pipeline(
    request: SearchRequest,
    *,
    searxng_url: str = _DEFAULT_SEARXNG_URL,
    providers: Sequence[SearchProvider] | None = None,
    configured_chain: Sequence[SearchProvider] | None = None,
    search_metrics: SearchMetricsSink | None = None,
    config: dict[str, Any],
    classifier: Any = None,
    promptguard_threshold: float = 0.85,
) -> SearchResponse:
    """Run a web search through a search provider chain with sanitized results.

    The backend is reached through the ``SearchProvider`` seam
    (``pipeline/search_providers/``) — ``SearxngProvider``, ``BraveApiProvider``
    — so this function owns orchestration, not HTTP. It then sanitizes every
    model-visible title, URL, and snippet through Stage 1 (HTML extraction),
    Stage 2 (structural scan), and one aggregate Stage 3 PromptGuard
    classification per result. At most ``_MAX_SEARCH_RESULTS_SCANNED``
    results are classified, bounding search-path inference work to 20
    PromptGuard passes.

    - BLOCKED result fields (Stage 2 or 3) omit the entire result.
    - SUSPICIOUS fields are included with a ``suspicious`` flag.
    - Providers are tried in chain order (free-first); the first to return a
      *sufficient* :class:`ProviderSearchResult` serves the request and no
      later provider is called. A :class:`ProviderFailure` — or an exception
      escaping ``search()``, treated as ``hard_error`` — advances to the next
      provider, and so does a :class:`ProviderSearchResult` with zero raw
      results and a non-empty ``unresponsive_engines`` list (recorded as
      ``"<name>: rate_limited"``): this is how SearXNG actually fails in
      production, a 200 that never raises. Sufficiency is judged on raw
      results before sanitization, so a poisoned or fail-closed result set
      that sanitization later empties out is still a success. A chain of
      exactly one ``searxng`` provider has nothing to fall back to, so that
      one shape is served as-is there instead of advancing. Replace-not-merge:
      a served response's ``results`` and ``unresponsive_engines`` come only
      from the serving provider; nothing from a failed provider survives into
      it. An exhausted chain raises :class:`PipelineError` — today's
      SearXNG-era codes, composed from the provider's closed ``detail``
      token, or ``search_unavailable`` with a reason composed from every
      provider tried.

    *providers* is the chain the lifespan resolved from
    ``FORAGE_SEARCH_PROVIDERS``, tried in order. ``None`` means "no chain
    supplied" and builds the default one-element SearXNG chain from
    *searxng_url* — the test call sites that still pass ``searxng_url=`` take
    this path. The check is ``is None`` and never a falsy one: an empty
    non-``None`` sequence is a caller programming error with no wire code,
    and a falsy check would silently serve the default chain instead of
    surfacing it.

    *configured_chain* is what the exhausted-chain 422 code is chosen from —
    the operator-**configured** chain, never the per-request effective one
    (ruling 28), so no status code varies with a policy filter. Defaults to
    *providers* when not supplied; spec 4 passes the configured chain
    explicitly once per-request policy can narrow *providers*.

    *search_metrics* is incremented directly during traversal: ``paid_calls``
    once per call to a ``paid=True`` provider (before the call, so a call that
    times out is still counted), and ``fallback_fired`` once per request in
    which traversal advances past the first provider — both move even when
    the chain is ultimately exhausted and the call ends in a raised
    :class:`PipelineError`. ``None`` (the default) is a private null object,
    so every increment site is unconditional.

    This function never reads the environment.
    """
    if providers is not None and len(providers) == 0:
        raise ValueError(
            "run_search_pipeline received an empty provider chain; a caller "
            "with no provider to offer must not call the pipeline"
        )

    request_id = uuid.uuid4().hex

    # -- Call the search provider chain, free-first --
    # Request extra results to compensate for any BLOCKED omissions. The
    # candidate budget is a *request* to the provider, never a trusted bound:
    # the slice below is re-applied to whatever comes back, so
    # `_MAX_SEARCH_RESULTS_SCANNED` stays enforced on this side of the seam.
    fetch_limit = min(request.num_results * 2, _MAX_SEARCH_RESULTS_SCANNED)
    chain: Sequence[SearchProvider] = (
        [SearxngProvider(searxng_url)] if providers is None else providers
    )
    resolved_configured_chain: Sequence[SearchProvider] = (
        chain if configured_chain is None else configured_chain
    )

    metrics: SearchMetricsSink = (
        search_metrics if search_metrics is not None else _NullSearchMetrics()
    )

    outcome: ProviderSearchResult | None = None
    serving_provider: SearchProvider | None = None
    serving_max_results: int | None = None
    provider_errors: list[str] = []
    last_failure: ProviderFailure | None = None
    last_provider: SearchProvider | None = None
    fallback_fired = False
    for index, provider in enumerate(chain):
        if index > 0 and not fallback_fired:
            fallback_fired = True
            metrics.fallback_fired += 1
        max_results = request.num_results if provider.paid else fetch_limit
        if provider.paid:
            metrics.paid_calls += 1
        try:
            call_outcome = await provider.search(request.query, max_results)
        except Exception:
            # Ruling 27 already guarantees every provider's own mapping ends
            # in this catch-all; this guard is a second, orchestrator-side
            # floor so a defect in provider *n* can never become a 500 or
            # skip the free floor at *n+1*.
            call_outcome = ProviderFailure(
                provider_name=provider.name,
                failure_class="hard_error",
                detail="unexpected",
            )

        if (
            isinstance(call_outcome, ProviderSearchResult)
            and not call_outcome.results
            and call_outcome.unresponsive_engines
        ):
            # Ruling 17's headline rule: a 200 with zero raw results and every
            # engine listed as unresponsive is how SearXNG actually fails in
            # production (kit_tools/docs/GOTCHAS.md "SearXNG :latest rots") —
            # it never raises, so it is only visible here. A chain of exactly
            # one `searxng` provider has nothing to fall back to, so this
            # shape is not a trigger there: it is served exactly as before
            # the provider seam existed.
            if _legacy_searxng_codes(resolved_configured_chain):
                logger.warning(
                    "search_provider_failed provider=%s failure_class=%s detail=%s",
                    provider.name,
                    "rate_limited",
                    "unresponsive_engines",
                )
                outcome = call_outcome
                serving_provider = provider
                serving_max_results = max_results
                break
            call_outcome = ProviderFailure(
                provider_name=provider.name,
                failure_class="rate_limited",
                detail="unresponsive_engines",
            )

        if isinstance(call_outcome, ProviderFailure):
            provider_errors.append(f"{provider.name}: {call_outcome.failure_class}")
            logger.warning(
                "search_provider_failed provider=%s failure_class=%s detail=%s",
                provider.name,
                call_outcome.failure_class,
                call_outcome.detail,
            )
            last_failure = call_outcome
            last_provider = provider
            continue

        outcome = call_outcome
        serving_provider = provider
        serving_max_results = max_results
        break

    if outcome is None or serving_provider is None or serving_max_results is None:
        if last_failure is None or last_provider is None:
            raise ValueError(
                "run_search_pipeline received an empty provider chain; a caller "
                "with no provider to offer must not call the pipeline"
            )
        if _legacy_searxng_codes(resolved_configured_chain):
            raise _searxng_pipeline_error(
                last_provider, last_failure, request_id=request_id
            )
        raise _search_unavailable_error(provider_errors, request_id=request_id)

    raw_results: list[dict[str, Any]] = outcome.results[:serving_max_results]
    unresponsive_engines: list[str] = [
        _normalize_search_text(name, max_length=_MAX_UNRESPONSIVE_ENGINE_LENGTH)
        for name in outcome.unresponsive_engines[:_MAX_UNRESPONSIVE_ENGINES]
    ]

    # -- Sanitize complete results through Stages 1-3 --
    sanitized_results: list[SearchResult] = []
    promptguard_started = time.perf_counter()
    promptguard_scanned = 0
    unscanned_results = 0
    promptguard_unavailable = False
    omitted_by_reason: Counter[str] = Counter()
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
            omitted_by_reason[contract.OMIT_INVALID_URL] += 1
            continue
        url, url_scan_text, domain = canonical_url
        snippet, snippet_scan_text = _sanitize_search_text(
            raw.get("content", ""),
            max_length=_MAX_SEARCH_SNIPPET_LENGTH,
        )
        engine = raw.get("engine")
        # `content_kind` describes the whole batch the provider returned;
        # `date` is per-result and is filtered to a strict calendar date by
        # `SearchResult` itself, so anything else becomes None there.
        result_date = raw.get("date")
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
            omitted_by_reason[contract.OMIT_STRUCTURAL_BLOCKED] += 1
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
        if pg_result.verdict == Stage3Verdict.INJECTION_DETECTED:
            if pg_result.skip_reason == "model_unavailable":
                logger.info(
                    "Omitting search result — PromptGuard unavailable "
                    "(fail-closed): %s",
                    url,
                )
                omitted_by_reason[contract.OMIT_PROMPTGUARD_UNAVAILABLE] += 1
                promptguard_unavailable = True
            else:
                logger.info(
                    "Omitting blocked search result (promptguard score=%.2f): %s",
                    pg_result.score,
                    url,
                )
                omitted_by_reason[contract.OMIT_INJECTION_DETECTED] += 1
            continue

        if pg_result.skipped and pg_result.skip_reason == "model_unavailable":
            # Fail-open pass-through: PromptGuard did not classify this result.
            unscanned_results += 1
            suspicious = True
            promptguard_unavailable = True
        else:
            promptguard_scanned += 1
            if pg_result.score > 0.5:
                suspicious = True

        sanitized_results.append(
            SearchResult(
                title=title,
                url=url,
                domain=domain,
                snippet=snippet,
                engine=engine if isinstance(engine, str) else None,
                content_kind=outcome.content_kind,
                date=result_date,
                suspicious=suspicious,
            )
        )

    omitted_results = sum(omitted_by_reason.values())
    promptguard_duration_ms = round(
        (time.perf_counter() - promptguard_started) * 1000,
        2,
    )
    logger.info(
        "search_promptguard_complete",
        extra={
            "scanned_results": promptguard_scanned,
            "max_scanned_results": _MAX_SEARCH_RESULTS_SCANNED,
            "omitted_results": omitted_results,
            "omitted_by_reason": dict(omitted_by_reason),
            "unscanned_results": unscanned_results,
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
        provider_used=serving_provider.name,
        fallback_fired=fallback_fired,
        provider_errors=provider_errors,
        unresponsive_engines=unresponsive_engines,
        omitted_results=omitted_results,
        omitted_by_reason=dict(omitted_by_reason),
        unscanned_results=unscanned_results,
        promptguard_unavailable=promptguard_unavailable,
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
