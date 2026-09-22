"""Pipeline orchestrator -- wires Stages 1-5 into a single retrieve/search flow.

The orchestrator is the entry point for ``POST /retrieve`` and
``POST /search``.  It calls each stage function in sequence with hard
gates: a BLOCKED verdict at Stage 2 or an INJECTION_DETECTED verdict at
Stage 3 halts the pipeline and returns a quarantine
:class:`RetrievedContent`.
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
import time
import unicodedata
import uuid
from collections import Counter
from collections.abc import AsyncGenerator, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol, get_args
from urllib.parse import SplitResult, unquote, urlparse, urlsplit, urlunsplit

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
    extract_pdf_bytes_in_subprocess,
    extract_pdf_in_subprocess,
)
from pipeline.retrieve_limits import RetrieveSettings
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
from pipeline.stage1_extraction import ExtractionResult, extract_html, normalize_text
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
from pipeline.stage3_promptguard import (
    PromptGuardResult,
    run_promptguard,
    unavailable_result,
)
from pipeline.stage4_structuring import (
    SanitizationResult,
    build_extracted_content,
    build_retrieved_content,
    structure_sanitization_result,
)
from pipeline.stage5_url_audit import ContentTooLargeError, fetch_url
from promptguard.classifier import PromptGuardBudgetExceededError
from url_validator import (
    BlockedDomainError,
    CanonicalHost,
    PrivateIPError,
    canonicalize_host,
    is_blocklisted_hostname,
    private_address_class,
    validate_url,
)

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


@asynccontextmanager
async def _bounded_permit(
    semaphore: asyncio.Semaphore,
    seconds: float | None,
) -> AsyncGenerator[bool]:
    """Hold one classification permit, waiting at most *seconds* for it.

    Yields ``True`` while the permit is held and ``False`` when the wait
    expired, so a caller branches on an outcome instead of catching an
    exception. ``None`` seconds waits without a deadline — the ``/extract``
    file route's untimed acquisition.

    The release is in ``finally`` and runs **only when the permit was
    acquired**: the deadline cancels the pending ``acquire()``, and a
    grant that lands after that cancellation is handed back by
    ``Semaphore.acquire``'s own cancellation handling. Releasing on the
    timeout path as well would hand out a permit this coroutine never held and
    grow the pool by one on every timeout.

    The shape is ``cache.py``'s ``_attempt_connect`` (``:405-425``), not a new
    one: one fixed deadline around exactly one awaited call, with a
    closed-token WARNING at the caller.
    """
    acquired = False
    try:
        try:
            # The deadline context takes ``None`` as its no-deadline form, so
            # the untimed `/extract` acquisition is this same one statement
            # rather than a second bare ``acquire()`` for a reviewer to check.
            async with asyncio.timeout(seconds):
                await semaphore.acquire()
        except TimeoutError:
            yield False
            return
        acquired = True
        yield True
    finally:
        if acquired:
            semaphore.release()


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
    classification_semaphore: asyncio.Semaphore | None = None,
    classification_wait_seconds: float | None = None,
) -> SanitizationResult:
    """Run the shared Stage 2-4 gauntlet for any extracted content source.

    *classification_semaphore* bounds stage 3 — and stage 3 only — across every
    route that classifies: the two fetch routes and the ``/extract`` file
    route, which moved its acquisition in here rather than wrapping stages 2
    and 4 with it. ``None`` acquires nothing, which is what the unguarded
    callers (``run_extract_pipeline``, direct callers) keep doing.

    *classification_wait_seconds* bounds the wait for that permit. ``None``
    waits without a deadline — the ``/extract`` file route, whose acquisition
    stays untimed and uncounted — and a float is ``/retrieve``'s
    ``promptguard_wait_seconds``, after which the request takes the
    classifier-unavailable outcome under its own policy instead of queueing.

    Stages 2 and 4 run on the default executor through ``asyncio.to_thread``,
    the way stage 3's inference already does, so a pathological page cannot
    stall ``/health`` on either route that comes through here. The functions
    are pure, so the output is byte-identical to the synchronous calls.
    """
    structural = await asyncio.to_thread(scan_structural, extraction.raw_text)
    promptguard = PromptGuardResult(
        verdict=Stage3Verdict.SAFE,
        score=0.0,
        skipped=True,
        skip_reason="structural_block",
    )

    async def classify() -> PromptGuardResult:
        return await run_promptguard(
            extraction.raw_text,
            classifier,
            threshold=promptguard_threshold,
            trust_tier=trust_tier,
            fail_closed=promptguard_fail_closed,
            max_chunks=max_promptguard_chunks,
        )

    if structural.verdict != Stage2Verdict.BLOCKED:
        # The permit is taken only under the condition `run_promptguard`
        # itself classifies on: a loaded classifier *and* a non-TRUSTED tier.
        # `stage3_promptguard.py` returns `skip_reason="trusted_tier"` before
        # the absent-classifier branch and before any inference, so a
        # `trusted_domains` page must never queue behind a 256-chunk one for
        # work it will not do. With either condition false nothing is
        # acquired and no counter moves.
        if (
            classification_semaphore is not None
            and classifier is not None
            and classifier.loaded
            and trust_tier != TrustTier.TRUSTED
        ):
            async with _bounded_permit(
                classification_semaphore, classification_wait_seconds
            ) as acquired:
                if acquired:
                    promptguard = await classify()
                else:
                    # Its own closed token, carrying nothing caller-derived.
                    # The classifier here is loaded and *busy*; the
                    # "PromptGuard unavailable" lines would send an operator
                    # to the model loader instead of to contention.
                    #
                    # `route=retrieve` is a literal because only `/retrieve`
                    # passes a deadline into this function: the `/extract`
                    # file route passes `classification_wait_seconds=None`,
                    # which cannot time out, and `/search` never calls here.
                    logger.warning("classification_wait_timeout route=retrieve")
                    promptguard = unavailable_result(
                        trust_tier.value, fail_closed=promptguard_fail_closed
                    )
        else:
            promptguard = await classify()

    return await asyncio.to_thread(
        structure_sanitization_result,
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
    settings: RetrieveSettings,
    retrieve_metrics: RetrieveMetricsSink,
    classification_semaphore: asyncio.Semaphore,
    extraction_settings: ExtractionSettings,
    admission: AdmissionSlot,
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
    settings:
        Boot-validated ``retrieve:`` limits. Required, not defaulted — a
        defaulted limits parameter would be the second, unbounded limits path
        this spec exists to prevent, exactly as
        ``run_extract_pipeline_from_file`` takes its ``ExtractionSettings``.
    retrieve_metrics:
        The ``/metrics`` retrieve counters this pipeline increments directly.
    classification_semaphore:
        Bounds concurrent PromptGuard work across both fetch routes.
    extraction_settings:
        The ``extraction:`` limits a fetched PDF's bounded worker runs under —
        ``app.state.extraction_settings``, whose character ceiling (not
        ``settings``') is the one a fetched PDF is refused against.
    admission:
        The ``/retrieve`` admission slot, ``app.state.retrieve_admission``.
        Acquired after the cache read (a hit never waits) and before the fetch
        (a queued request holds no body), with no timer around the
        acquisition, and released in ``finally`` once stage 1 is done — so
        the fetched body goes with the slot, before the classification wait.
        A full queue is refused 422 ``busy`` / ``admission_queue_full``.

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
    classifier_loaded = classifier is not None and classifier.loaded

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
        classifier_loaded=classifier_loaded,
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

    # -- Step 3: Take an admission slot, fetch, and run Stage 1 --
    # After the cache read, so a hit never waits, and before the fetch, so a
    # queued request holds no body. No timer around `acquire()`: the
    # controller's handoff is not cancellation-safe after a grant (recorded in
    # GOTCHAS.md), and its bounded queue depth and reserved bytes are the
    # backpressure — a full queue refuses at once rather than waiting.
    if not await admission.acquire():
        raise PipelineError(
            error="busy",
            reason=contract.RETRIEVE_ADMISSION_QUEUE_FULL,
            request_id=request_id,
        )
    try:
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
            # `/extract`'s spawned, rlimited worker, from a 0600 file in the
            # process-private spool directory — so a fetched PDF runs under
            # `extraction.max_promptguard_chunks`, the ceiling the worker's
            # rlimits were sized for, not `retrieve.max_promptguard_chunks`.
            # Most-specific first: every PDF failure is a `PDFExtractionError`
            # subclass, and the classifiable-text refusal is not a parse
            # failure.
            pdf_worker = asyncio.create_task(
                asyncio.to_thread(
                    extract_pdf_bytes_in_subprocess,
                    fetch_result.response_body,
                    extraction_settings,
                )
            )
            cancellation: asyncio.CancelledError | None = None
            try:
                # Cancelling a to_thread await cannot stop its thread. Keep the
                # slot until the bounded worker is reaped and its spool unlinked,
                # even on repeated cancellation. wait() leaves the task intact;
                # result() below retrieves its outcome, including host faults.
                while not pdf_worker.done():
                    try:
                        await asyncio.wait({pdf_worker})
                    except asyncio.CancelledError as exc:
                        if cancellation is None:
                            cancellation = exc
                extraction = pdf_worker.result()
            except PDFClassifiableTextLimitError as exc:
                raise PipelineError(
                    error="content_too_large",
                    reason=contract.PROMPTGUARD_BUDGET,
                    request_id=request_id,
                ) from exc
            except PDFEncryptedError as exc:
                raise PipelineError(
                    error="extraction_failed",
                    reason=contract.RETRIEVE_PDF_ENCRYPTED,
                    request_id=request_id,
                ) from exc
            except PDFNoTextError as exc:
                raise PipelineError(
                    error="extraction_failed",
                    reason=contract.RETRIEVE_PDF_NO_TEXT,
                    request_id=request_id,
                ) from exc
            except PDFExtractionError as exc:
                raise PipelineError(
                    error="extraction_failed",
                    reason=contract.RETRIEVE_PDF_EXTRACTION_ERROR,
                    request_id=request_id,
                ) from exc
            except OSError as exc:
                # The one host fault in the table (ENOSPC, EACCES, a read-only
                # or vanished temp dir, a refused spool directory). `/metrics`
                # keys errors by code alone, so this closed token — nothing
                # path- or content-derived — is what lets an alert tell it
                # apart from an encrypted-PDF caller.
                logger.warning("retrieve_spool_error")
                raise PipelineError(
                    error="extraction_failed",
                    reason=contract.RETRIEVE_PDF_SPOOL_ERROR,
                    request_id=request_id,
                ) from exc
            finally:
                if cancellation is not None:
                    raise cancellation
        else:
            html_text = fetch_result.response_body.decode("utf-8", errors="replace")
            extraction = await asyncio.to_thread(extract_html, html_text, request.url)
            del html_text
        # The three post-stage-1 scalars leave the fetch result here, so the
        # body and its decoded copy go with the slot: a request parked on the
        # classification permit holds only its extracted text.
        final_url = fetch_result.final_url
        redirect_chain = fetch_result.redirect_chain
        domain_changed_on_redirect = fetch_result.domain_changed_on_redirect
        del fetch_result
    finally:
        await admission.release()

    # -- Step 4a: Refuse an over-budget page before classifying it --
    # The primary control: a page whose extracted text exceeds the character
    # ceiling derived from `retrieve.max_promptguard_chunks` is refused rather
    # than chunked and classified in full, so one hostile page cannot burn
    # unbounded CPU. Characters only -- `/extract`'s second, byte limb is not
    # copied here: at the coming default of 256 chunks the character limb
    # (458 752) admits at most 1 835 008 UTF-8 bytes, under the 2 MiB output
    # ceiling, so a byte limb could not bind below 293 chunks.
    budget_characters = settings.max_extracted_characters
    if budget_characters is not None and len(extraction.raw_text) > budget_characters:
        raise PipelineError(
            error="content_too_large",
            reason=contract.PROMPTGUARD_BUDGET,
            request_id=request_id,
        )

    # Determine domain from final URL
    parsed_final = urlparse(final_url)
    domain = parsed_final.hostname or ""
    trust_tier = TrustTier(
        _resolve_request_trust_tier(
            domain,
            request.trusted_domains,
            request.verified_domains,
            blocked_domains,
        )
    )
    # The catch is the backstop, not the control: the pre-check above already
    # refused an over-budget page, and this maps the classifier's own refusal
    # to the same 422 should the two ever disagree.
    try:
        sanitization = await sanitize_and_structure(
            extraction=extraction,
            trust_tier=trust_tier,
            classifier=classifier,
            promptguard_threshold=request.promptguard_threshold,
            promptguard_fail_closed=request.promptguard_fail_closed,
            extract_mode=request.extract_mode,
            content_type=content_type,
            domain_changed_on_redirect=domain_changed_on_redirect,
            max_promptguard_chunks=(
                settings.max_promptguard_chunks
                if settings.max_promptguard_chunks > 0
                else None
            ),
            classification_semaphore=classification_semaphore,
            classification_wait_seconds=settings.promptguard_wait_seconds,
        )
    except PromptGuardBudgetExceededError as exc:
        raise PipelineError(
            error="content_too_large",
            reason=contract.PROMPTGUARD_BUDGET,
            request_id=request_id,
        ) from exc
    if sanitization.injection_detected:
        logger.warning(
            "Content quarantined for %s — returning content-free response",
            request.url,
        )
    content = build_retrieved_content(
        request_id=request_id,
        source_url=request.url,
        final_url=final_url,
        domain=domain,
        sanitization=sanitization,
        redirect_chain=redirect_chain,
        domain_changed_on_redirect=domain_changed_on_redirect,
    )

    # A loaded classifier cannot produce an `unavailable_*` state through
    # `run_promptguard` — `stage3_promptguard.py` returns `model_unavailable`
    # only when the classifier is absent or still warming — so the
    # combination is exactly and only the classification-wait timeout above,
    # under either policy (`unavailable_blocked` fail-closed,
    # `unavailable_allowed` fail-open). Derived rather than threaded back so
    # the counter and the cache condition below read the same fact.
    wait_timed_out = classifier_loaded and content.promptguard_state in {
        "unavailable_blocked",
        "unavailable_allowed",
    }
    if wait_timed_out:
        retrieve_metrics.classification_wait_timeouts += 1

    # -- Step 8: Cache safe result --
    #
    # A wait-timeout body never enters the content cache. `cache.py`'s
    # `cache_policy_fingerprint` note explains why `classifier_loaded` is a
    # key input: before this story an unscanned body could only exist while
    # the flag was `False`, and the model loading orphaned it. A fail-open
    # wait timeout is the first unscanned body with `classifier_loaded=True`,
    # so without the condition below a saturation event lasting
    # `promptguard_wait_seconds` would let an in-network caller pin an
    # attacker-chosen unscanned body for `cache_ttl_hours` and replay it to
    # every later request — including ones a free permit would have
    # classified. The absent-classifier fail-open body still caches under its
    # `classifier_loaded=False` key exactly as before.
    if (
        cache is not None
        and request.cache_ttl_hours > 0
        and not content.injection_detected
        and not (
            content.promptguard_state == "unavailable_allowed" and classifier_loaded
        )
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
        # Stage 3 only, and exactly once: the permit used to wrap stages 2, 3
        # and 4 out here, which would only lengthen as those stages move off
        # the event loop. Untimed and uncounted — `/extract` is the
        # authenticated route and keeps waiting — and never both an outer and
        # an inner acquisition, which on a size-1 semaphore is a deadlock.
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
            classification_semaphore=classification_semaphore,
            classification_wait_seconds=None,
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
# `SearchResult.engine` is provider-controlled provenance metadata (which
# configured engine answered), not page content — it is bounded and
# normalized like `unresponsive_engines` but, deliberately, never
# structurally scanned or part of the PromptGuard input (contract 1.3.0).
_MAX_SEARCH_ENGINE_LENGTH = 64
# A bound on what `extract_html` parses, not a contract cap. `title` and
# `snippet` are now truncated *after* extraction, so without this the parser
# would be handed the provider's whole body (up to 1 MiB) per field per result.
# The multiplier is measured, not picked: the parser's cost is superlinear on
# unclosed-tag input, so 4x is a ~2 s lever on an unauthenticated, undeadlined
# route where 8x is a ~6 s one. Any change re-derives from the three-shape
# table in `kit_tools/specs/feature-hardening-search-sanitization.md`.
_SEARCH_PARSER_INPUT_MULTIPLIER = 4
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


def _scan_forms_for_search_text(value: object, *, max_length: int) -> tuple[str, str]:
    """Return ``(wire_form, scan_form)`` for one model-visible search text field.

    The scan form keeps line breaks so Stage 2's line-anchored BLOCK patterns
    (``^System:``, ``^POPPY:``, ``^assistant:`` under ``MULTILINE``) fire on any
    line of a title or snippet, exactly as they do on a fetched page. The wire
    form is the single-line text ``/search`` has always served.

    Guarantees:

    * ``wire_form == " ".join(scan_form.split())`` -- the wire form is derived
      from the scanned string, never built alongside it.
    * Every non-whitespace character of the wire form appears, in order, in the
      scan form, so nothing reaches the wire unscanned.
    * Truncation happens once, on the scan form, before the wire form is
      derived, so blank-line padding cannot push a payload past the scan and
      leave it on the wire.

    Both returned forms are scanned by the caller, and neither alone is
    sufficient. The scan form is what makes the line-anchored BLOCK patterns
    fire per line; the wire form is what makes the patterns compiled without
    ``re.DOTALL`` fire across what was a line break. Scanning only one of them
    is a bypass in whichever direction that pattern class runs.

    There are two control-character strips because there are two sources. The
    first runs on the raw provider value: the HTML parser maps a raw NUL to
    U+FFFD, which is outside ``_CONTROL_CHARS_RE``'s class, so a raw control
    stripped only afterwards would ship as a replacement character. The second
    runs after both decode levels -- the parser's one entity level plus
    ``html.unescape`` -- because those decodes mint C0/C1 characters of their
    own, and ``stage1_extraction._normalize_text`` removes only nine zero-width
    and bidi code points, not the C0/C1 range.
    """
    if not isinstance(value, str):
        return ("", "")
    text = unicodedata.normalize("NFC", value)
    text = _CONTROL_CHARS_RE.sub("", text)
    text = text[: _SEARCH_PARSER_INPUT_MULTIPLIER * max_length]
    extraction = extract_html(f"<div>{text}</div>")
    scan_form = html.unescape(extraction.raw_text)
    scan_form = _CONTROL_CHARS_RE.sub("", scan_form)
    scan_form = normalize_text(scan_form)[:max_length]
    return (" ".join(scan_form.split()), scan_form)


SearchUrlRule = Literal[
    "missing",
    "too_long",
    "raw_chars",
    "unparseable",
    "invalid_port",
    "parse",
    "userinfo",
    "host_code_point",
    "zone_id",
    "numeric_host",
    "idna",
]
"""Closed vocabulary for why `_canonicalize_search_url` rejected a result URL.

The token is content-free: it names the rule that fired, never the URL or its
host (invariant 6). `run_search_pipeline` logs it as `search_url_rejected
rule=<token> provider=<name>` and `kit_tools/docs/MONITORING.md` aggregates on
that pair. It stays internal -- the wire reason is the `contract.OMIT_*`
constant carried beside it.
"""

SEARCH_URL_RULES = frozenset(get_args(SearchUrlRule))

SearchHostClass = Literal[
    "private_literal",
    "embedded_private",
    "blocklisted_name",
]
"""Closed vocabulary for why the search-time audit *blocked* a result URL.

Deliberately disjoint from `SearchUrlRule`: a rejected URL is malformed and
logs `search_url_rejected`, a blocked one is well-formed and points somewhere
policy refuses, and logs `search_url_blocked host_class=<token>`. The two
vocabularies never share a token, so an operator aggregating on one is never
reading the other's records. Like `SearchUrlRule` it is content-free -- it
names the class, never the host.
"""

SEARCH_HOST_CLASSES = frozenset(get_args(SearchHostClass))


@dataclass(frozen=True, slots=True)
class SearchUrlOutcome:
    """The verdict on one result URL -- internal, never the wire shape.

    Exactly two shapes. A cleared URL carries `canonical_url`, `domain` and the
    two `scan_texts`, with `omission_reason` and `rule` both `None`; a
    rejected or blocked one carries the reason pair and leaves the other three
    empty. `domain` is never derived from a URL that did not clear every rule.

    `rule` carries either vocabulary: a `SearchUrlRule` beside
    `contract.OMIT_INVALID_URL`, a `SearchHostClass` beside
    `contract.OMIT_BLOCKED_URL`. Widening the carrier rather than the
    `SearchUrlRule` `Literal` is what keeps the two log vocabularies disjoint.

    `scan_texts` is `(entity-decoded, once-percent-decoded)` -- the two forms
    Stage 2 scans. Neither is routed through `extract_html`: the extractor eats
    tag-shaped text, which is how an envelope tag could ride a path onto the
    wire unscanned.
    """

    canonical_url: str | None
    scan_texts: tuple[str, str]
    domain: str | None
    omission_reason: str | None
    rule: SearchUrlRule | SearchHostClass | None


@dataclass(frozen=True, slots=True)
class _UrlState:
    """The value threaded through `_SEARCH_URL_RULES`, one rule at a time."""

    raw: object
    value: str = ""
    parsed: SplitResult | None = None
    port: int | None = None
    canonical: CanonicalHost | None = None


def _reject_search_url(rule: SearchUrlRule) -> SearchUrlOutcome:
    """Build the rejection outcome for *rule*."""
    return SearchUrlOutcome(
        canonical_url=None,
        scan_texts=("", ""),
        domain=None,
        omission_reason=contract.OMIT_INVALID_URL,
        rule=rule,
    )


def _block_search_url(host_class: SearchHostClass) -> SearchUrlOutcome:
    """Build the blocked outcome for *host_class*.

    Separate from `_reject_search_url` because the wire reason differs:
    `blocked_url` is policy on a well-formed URL, `invalid_url` is
    malformation. Counting them together would hide "a provider is returning
    internal addresses" inside "a provider is returning junk".
    """
    return SearchUrlOutcome(
        canonical_url=None,
        scan_texts=("", ""),
        domain=None,
        omission_reason=contract.OMIT_BLOCKED_URL,
        rule=host_class,
    )


# Everything a URL may not carry in the raw provider value: C0 controls, space
# and tab/LF/CR (`\x00-\x20`), DEL and C1 (`\x7f-\x9f`), any other Unicode
# whitespace, and RFC 3986's excluded set. Rejection, never deletion --
# `_normalize_search_text` used to delete these, which is how
# `http://example.com/\x01foo` was served pointing at a different resource.
_RAW_URL_REJECT_RE = re.compile(r'[\x00-\x20\x7f-\x9f\s<>"{}|\\^`]')

# WHATWG's forbidden domain code points. `urlsplit` consumes `/ ? # @ [ ]`
# structurally and `:` for `host:port`, so what actually survives into
# `parsed.hostname` is `% \ < > ^ |`, space and control characters.
_FORBIDDEN_DOMAIN_CODE_POINTS = frozenset(
    [chr(code_point) for code_point in range(0x20)] + list("\x7f #%/:<>?@[\\]^|")
)


def _url_rule_presence_and_length(state: _UrlState) -> _UrlState | SearchUrlOutcome:
    """Rule (0): a non-empty string of at most `_MAX_SEARCH_URL_LENGTH` characters."""
    if not isinstance(state.raw, str):
        return _reject_search_url("missing")
    value = state.raw.strip()
    if not value:
        return _reject_search_url("missing")
    if len(value) > _MAX_SEARCH_URL_LENGTH:
        # Rejection, never truncation: a shortened URL points at a different
        # resource, and nothing downstream -- `html.unescape`, `unquote`,
        # `urlsplit`, `scan_structural` -- may be handed more than the bound.
        # `scan_structural` has no input cap of its own and `_line_number_of`
        # is O(n) per match, so an unbounded URL is a CPU lever.
        return _reject_search_url("too_long")
    return replace(state, value=value)


def _url_rule_raw_character_class(state: _UrlState) -> _UrlState | SearchUrlOutcome:
    """Rule (1): reject controls, whitespace and RFC 3986's excluded characters."""
    if _RAW_URL_REJECT_RE.search(state.value):
        return _reject_search_url("raw_chars")
    return state


def _url_rule_parse(state: _UrlState) -> _UrlState | SearchUrlOutcome:
    """Rule (2): parse, read the port, and require a bare HTTP(S) origin."""
    try:
        parsed = urlsplit(state.value)
    except ValueError:
        # A bracketed IPv6 literal is validated eagerly, so `[fe80::zz]` raises
        # here rather than yielding a host nothing downstream can read.
        return _reject_search_url("unparseable")
    try:
        port = parsed.port
    except ValueError:
        # `urlsplit("http://example.com:99999/")` parses fine with
        # `hostname == "example.com"`; it is the port read that raises. Both
        # reads live in this rule, and `port` travels on in `_UrlState`, so the
        # canonicalisation tail never touches `parsed.port` itself -- an
        # unhandled `ValueError` there would be a 500 on an unauthenticated
        # route from a provider-supplied URL.
        return _reject_search_url("invalid_port")
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return _reject_search_url("parse")
    if parsed.username is not None or parsed.password is not None:
        return _reject_search_url("userinfo")
    return replace(state, parsed=parsed, port=port)


def _url_rule_host_code_points(state: _UrlState) -> _UrlState | SearchUrlOutcome:
    """Rule (3): no forbidden domain code point, and no IPv6 zone id."""
    parsed = state.parsed
    if parsed is None or not parsed.hostname:
        return _reject_search_url("parse")
    host = parsed.hostname
    if ":" in host:
        # An IPv6 literal -- `urlsplit` has already stripped the brackets. Its
        # colons are exempt; a `%25` zone id is not.
        if "%" in host:
            return _reject_search_url("zone_id")
        forbidden = _FORBIDDEN_DOMAIN_CODE_POINTS - {":"}
    else:
        forbidden = _FORBIDDEN_DOMAIN_CODE_POINTS
    if any(character in forbidden for character in host):
        return _reject_search_url("host_code_point")
    return state


def _url_rule_canonicalize_host(state: _UrlState) -> _UrlState | SearchUrlOutcome:
    """Rule (3a): canonicalise the host, literals first. No DNS, ever.

    `canonicalize_host` is the one canonicaliser and the one UTS-46 call site
    in this service. `validate_url` is deliberately *not* reachable from here:
    it resolves DNS, and Forage does not look up a URL nobody asked to fetch.
    """
    parsed = state.parsed
    if parsed is None or not parsed.hostname:
        return _reject_search_url("parse")
    canonical = canonicalize_host(parsed.hostname)
    if not isinstance(canonical, CanonicalHost):
        # The token is read off the rejection, never recomputed -- the caller
        # may not re-run the encode to learn why it failed.
        return _reject_search_url(canonical.reason)
    return replace(state, canonical=canonical)


def _url_rule_address_class(state: _UrlState) -> _UrlState | SearchUrlOutcome:
    """Rule (3b): an address literal that is private, or embeds one, is blocked."""
    canonical = state.canonical
    if canonical is None or canonical.address is None:
        return state
    host_class = private_address_class(canonical.address)
    if host_class is not None:
        # Decided by the helper, never recomputed here: the token says *how*
        # the address was reached, which a second `in`-check could not.
        return _block_search_url(host_class)
    return state


def _url_rule_blocklisted_name(state: _UrlState) -> _UrlState | SearchUrlOutcome:
    """Rule (3c): a name on the built-in private-name list is blocked."""
    canonical = state.canonical
    if canonical is None or canonical.kind != "name":
        return state
    if is_blocklisted_hostname(canonical.host):
        return _block_search_url("blocklisted_name")
    return state


# Ordered registry, first rejection wins: the order is data and each rule is a
# pure function, unit-testable on its own. The shape follows
# `pipeline/stage2_structural.py`'s `_PATTERNS` -- name-first pairs iterated in
# order.
_SEARCH_URL_RULES: tuple[
    tuple[str, Callable[[_UrlState], _UrlState | SearchUrlOutcome]], ...
] = (
    ("presence_and_length", _url_rule_presence_and_length),
    ("raw_character_class", _url_rule_raw_character_class),
    ("parse", _url_rule_parse),
    ("host_code_points", _url_rule_host_code_points),
    ("canonicalize_host", _url_rule_canonicalize_host),
    ("address_class", _url_rule_address_class),
    ("blocklisted_name", _url_rule_blocklisted_name),
)


def _canonicalize_search_url(value: object) -> SearchUrlOutcome:
    """Bound, screen and canonicalize one provider-supplied result URL.

    Iterates `_SEARCH_URL_RULES` over the **raw** provider value, first
    rejection wins, and canonicalizes only a value every rule cleared.
    `domain` is `CanonicalHost.host`, which for an IPv6 literal is the raw
    unbracketed literal (``2606:4700::1111``) even though `canonical_url`
    carries the bracketed form (``[2606:4700::1111]``) -- the one case where
    `domain` is not a substring of `canonical_url`.
    """
    state = _UrlState(raw=value)
    for _rule_name, rule in _SEARCH_URL_RULES:
        outcome = rule(state)
        if isinstance(outcome, SearchUrlOutcome):
            return outcome
        state = outcome

    parsed = state.parsed
    if parsed is None or not parsed.hostname or state.canonical is None:
        return _reject_search_url("parse")
    # `domain` is the canonicalised ASCII host; `canonical_url` keeps the
    # provider's spelling of it. The two diverge for an IDN host -- `domain`
    # is punycode, `url` is not -- because Goal 2 freezes the served URL.
    domain = state.canonical.host
    raw_host = parsed.hostname.lower()
    host = f"[{raw_host}]" if ":" in raw_host else raw_host
    netloc = host if state.port is None else f"{host}:{state.port}"
    canonical = urlunsplit(
        (
            parsed.scheme.lower(),
            netloc,
            parsed.path,
            parsed.query,
            "",
        )
    )
    # Rule (4)'s two texts, built from the trimmed raw value rather than the
    # canonical form. Exactly one `unquote` pass: `%253C...` stays encoded on
    # the wire and is out of scope. Both are at most `_MAX_SEARCH_URL_LENGTH`
    # characters -- rule (0) bounded the value and neither decode lengthens it.
    unescaped = html.unescape(state.value)
    return SearchUrlOutcome(
        canonical_url=canonical,
        scan_texts=(unescaped, unquote(unescaped)),
        domain=domain,
        omission_reason=None,
        rule=None,
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


class AdmissionSlot(Protocol):
    """One admission slot ``run_retrieve_pipeline`` holds for the work it does.

    ``retrieval_app.ExtractionAdmissionController`` satisfies this
    structurally — declared here, on the consumer side, because ``pipeline/``
    never imports ``retrieval_app`` (the rule :class:`SearchMetricsSink`
    records and ``cache.py`` records from the other end).

    ``acquire`` returns ``False`` when the queue is full rather than raising,
    so the caller decides what refusal the wire carries.
    """

    async def acquire(self) -> bool: ...

    async def release(self) -> None: ...


class AdmissionMetrics(Protocol):
    """The two admission counters an :class:`AdmissionSlot` increments.

    One Protocol for the pair, not two, because the controller increments both
    or neither: a saturated semaphore and a refused request are the two halves
    of the same story.
    """

    semaphore_saturation: int
    busy_rejections: int


class RetrieveMetricsSink(AdmissionMetrics, Protocol):
    """The ``/metrics`` retrieve counters ``run_retrieve_pipeline`` increments.

    ``retrieval_app.RetrieveMetrics`` satisfies this structurally, the same
    seam shape as :class:`SearchMetricsSink`.
    """

    classification_wait_timeouts: int


class _NullRetrieveMetrics:
    """A real counter nobody reads, for callers with no sink to hand over.

    The ``_NullSearchMetrics`` idiom: process-local scratch space satisfying
    :class:`RetrieveMetricsSink` structurally, so an increment site never
    needs an ``is not None`` branch.
    """

    def __init__(self) -> None:
        self.semaphore_saturation = 0
        self.busy_rejections = 0
        self.classification_wait_timeouts = 0


# Structural conformance, checked by the type checker rather than asserted in
# prose: a counter added to `RetrieveMetricsSink` without a matching field on
# the null sink is an error here, at the seam, instead of an `AttributeError`
# in whichever later story first increments it.
_NULL_RETRIEVE_METRICS: RetrieveMetricsSink = _NullRetrieveMetrics()


class SearchMetricsSink(Protocol):
    """The two ``/metrics`` search counters ``run_search_pipeline`` increments directly.

    ``retrieval_app.SearchMetrics`` satisfies this structurally — neither
    module imports the other. Declaring it here, on the consumer side, is the
    same seam shape as :class:`SearchProvider`.
    """

    fallback_fired: int
    paid_calls: int
    classification_wait_timeouts: int


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
        self.classification_wait_timeouts = 0


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
    classification_semaphore: asyncio.Semaphore | None = None,
    classification_wait_seconds: float | None = None,
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
    so every increment site is unconditional. ``classification_wait_timeouts``
    moves at most once per request, below.

    *classification_semaphore* is the same permit the two other classifying
    routes take, held around one result's Stage 3 call and released between
    results. *classification_wait_seconds* is a **per-request** budget, not a
    per-result one: one deadline is computed before the result loop, and once
    it passes, that result and every remaining one take the
    classifier-unavailable branch under the effective ``promptguard_fail_closed``
    without touching the semaphore. Both default to ``None`` — no permit, no
    deadline, today's behaviour — so no existing call site changes.

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

    # One deadline per request, tested explicitly before every acquisition.
    # The explicit test is what makes "every remaining result is unscanned"
    # true: `Semaphore.acquire()` returns without yielding when a permit is
    # free, so a zero-length deadline around it never fires, and a
    # permit released mid-loop after the deadline would otherwise be taken
    # and the result classified.
    loop = asyncio.get_running_loop()
    classification_deadline: float | None = (
        loop.time() + classification_wait_seconds
        if classification_semaphore is not None
        and classification_wait_seconds is not None
        else None
    )
    wait_expired = False

    def wait_timed_out() -> PromptGuardResult:
        """Take the classifier-unavailable outcome, counted once per request."""
        nonlocal wait_expired
        if not wait_expired:
            wait_expired = True
            # The classifier is loaded and busy, not absent: its own closed
            # token, nothing caller-derived.
            logger.warning("classification_wait_timeout route=search")
            metrics.classification_wait_timeouts += 1
        return unavailable_result(
            TrustTier.STANDARD.value,
            fail_closed=request.promptguard_fail_closed,
        )

    for raw in raw_results:
        if len(sanitized_results) >= request.num_results:
            break

        title, title_scan_text = _scan_forms_for_search_text(
            raw.get("title", ""),
            max_length=_MAX_SEARCH_TITLE_LENGTH,
        )
        url_outcome = _canonicalize_search_url(raw.get("url", ""))
        url = url_outcome.canonical_url
        domain = url_outcome.domain
        omission_reason = url_outcome.omission_reason
        if omission_reason is not None or url is None or domain is None:
            # Content-free: the token and the provider name, never the URL or
            # its host (invariant 6). An operator watching `invalid_url` or
            # `blocked_url` climb needs to know which rule or host class fired
            # on which provider, not the bytes -- and the two records carry
            # disjoint vocabularies, so aggregating on one never picks up the
            # other. `contract.OMIT_INVALID_URL` is the floor -- a cleared
            # outcome always carries both halves.
            if omission_reason == contract.OMIT_BLOCKED_URL:
                logger.info(
                    "search_url_blocked host_class=%s provider=%s",
                    url_outcome.rule,
                    serving_provider.name,
                )
            else:
                logger.info(
                    "search_url_rejected rule=%s provider=%s",
                    url_outcome.rule,
                    serving_provider.name,
                )
            omitted_by_reason[omission_reason or contract.OMIT_INVALID_URL] += 1
            continue
        snippet, snippet_scan_text = _scan_forms_for_search_text(
            raw.get("content", ""),
            max_length=_MAX_SEARCH_SNIPPET_LENGTH,
        )
        engine = _normalize_search_text(
            raw.get("engine"), max_length=_MAX_SEARCH_ENGINE_LENGTH
        )
        # `content_kind` describes the whole batch the provider returned;
        # `date` is per-result and is filtered to a strict calendar date by
        # `SearchResult` itself, so anything else becomes None there.
        result_date = raw.get("date")
        suspicious = False

        # Stage 2: scan every model-visible field before exposing the result.
        blocked = False
        for field_name, field_text in (
            # Both forms of each text field, for the same reason rule (4)
            # scans two URL texts: the loop's break/flag behaviour is the
            # BLOCKED > SUSPICIOUS > clean ladder, so the worse verdict wins
            # without a second comparator.
            #
            # The scan form keeps line breaks so the line-anchored patterns
            # fire per line; the wire form is its whitespace collapse. Neither
            # is a superset of the other for Stage 2's purposes: a pattern
            # compiled without `re.DOTALL` -- `disregard.*instructions`
            # (stage2_structural.py, BLOCK) and the `!\[.*?\]\(` exfil beacon
            # (SUSPICIOUS) are the two such patterns among the 24 registered --
            # matches across a space but not across a newline. Scanning only
            # the newline-preserving form therefore served a payload that its
            # own collapsed wire form would have blocked. Scan both.
            ("title", title_scan_text),
            ("title", title),
            ("url", url_outcome.scan_texts[0]),
            ("url", url_outcome.scan_texts[1]),
            ("snippet", snippet_scan_text),
            ("snippet", snippet),
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
        #
        # The acquisition guard is the condition `run_promptguard` itself
        # classifies on — a classifier that is present *and* loaded. A warming
        # model is not None and not loaded, which is exactly when the other
        # two routes are contending for the same permit.
        pg_result: PromptGuardResult
        if (
            classification_semaphore is None
            or classifier is None
            or not classifier.loaded
        ):
            pg_result = await run_promptguard(
                _search_result_promptguard_input(title, url, snippet),
                classifier,
                threshold=promptguard_threshold,
                trust_tier="standard",
                fail_closed=request.promptguard_fail_closed,
            )
        elif wait_expired or (
            classification_deadline is not None
            and loop.time() >= classification_deadline
        ):
            pg_result = wait_timed_out()
        else:
            async with _bounded_permit(
                classification_semaphore,
                (
                    None
                    if classification_deadline is None
                    else classification_deadline - loop.time()
                ),
            ) as acquired:
                if acquired:
                    pg_result = await run_promptguard(
                        _search_result_promptguard_input(title, url, snippet),
                        classifier,
                        threshold=promptguard_threshold,
                        trust_tier="standard",
                        fail_closed=request.promptguard_fail_closed,
                    )
                else:
                    pg_result = wait_timed_out()
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
                engine=engine or None,
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
