"""Stage 4 -- Content structuring and trust score computation.

Assembles final response objects from the outputs of Stages 1-3 plus
source metadata. Computes a composite trust score using the trust tier
resolved by the source-specific orchestrator.

Summary mode uses :func:`extract_summary` from the smart extraction
module to preserve high-signal content (statistics, quotes, references)
while trimming filler.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from models import (
    ExtractedContent,
    RetrievedContent,
    Stage2Verdict,
    Stage3Verdict,
    TrustTier,
    UploadProvenance,
)
from pipeline.contract import (
    DIAG_INJECTION_DETECTED,
    DIAG_PROMPTGUARD_UNAVAILABLE,
    DIAG_STRUCTURAL_BLOCKED,
    PromptGuardState,
)
from pipeline.smart_extraction import extract_summary
from pipeline.stage1_extraction import ExtractionResult
from pipeline.stage2_structural import StructuralScanResult
from pipeline.stage3_promptguard import PromptGuardResult

# ---------------------------------------------------------------------------
# Trust tier base scores
# ---------------------------------------------------------------------------

_BASE_SCORES: dict[TrustTier, float] = {
    TrustTier.TRUSTED: 0.95,
    TrustTier.VERIFIED: 0.85,
    TrustTier.STANDARD: 0.70,
    TrustTier.UNTRUSTED: 0.40,
    TrustTier.BLOCKED: 0.0,
}

_REDIRECT_DOMAIN_CHANGE_PENALTY = -0.1
_QUARANTINE_BODY = "Content quarantined due to potential prompt injection."


@dataclass(frozen=True, slots=True)
class SanitizationResult:
    """Source-neutral Stage 4 output shared by URL and upload responses."""

    title: str | None
    body: str
    word_count: int
    content_type: str
    trust_score: float
    trust_tier: TrustTier
    injection_detected: bool
    injection_spans: list[str]
    structural_flags: list[str]
    stage2_verdict: Stage2Verdict
    stage3_verdict: Stage3Verdict
    promptguard_state: PromptGuardState
    truncation_notice: str | None
    sanitizer_revision: str


# ---------------------------------------------------------------------------
# Trust score computation
# ---------------------------------------------------------------------------


def _compute_trust_score(
    tier: TrustTier,
    stage2_penalty: float,
    stage3_penalty: float,
    domain_changed_on_redirect: bool,
) -> float:
    """Compute composite trust score from base score minus penalties.

    Returns a value clamped to [0.0, 1.0].
    """
    base = _BASE_SCORES.get(tier, 0.0)
    redirect_penalty = (
        _REDIRECT_DOMAIN_CHANGE_PENALTY if domain_changed_on_redirect else 0.0
    )
    # Penalties are already negative, so we add them
    score = base + stage2_penalty + stage3_penalty + redirect_penalty
    return max(0.0, min(1.0, score))


def _derive_promptguard_state(
    *,
    promptguard: PromptGuardResult,
    stage2_verdict: Stage2Verdict,
) -> PromptGuardState:
    """Classify how PromptGuard examined this document for the wire.

    The stage-2 BLOCKED verdict is the authoritative backstop for
    ``structural_blocked``: direct ``structure_sanitization_result`` callers
    may pass a ``promptguard`` whose ``skip_reason`` was never set, but a
    document PromptGuard never examined because stage 2 already blocked it
    must never be reported as ``scanned``.
    """
    if promptguard.skip_reason == "trusted_tier":
        return "skipped_trusted"
    if (
        promptguard.skip_reason == "structural_block"
        or stage2_verdict == Stage2Verdict.BLOCKED
    ):
        return "structural_blocked"
    if promptguard.skip_reason == "model_unavailable":
        if promptguard.verdict == Stage3Verdict.INJECTION_DETECTED:
            return "unavailable_blocked"
        return "unavailable_allowed"
    return "scanned"


def structure_sanitization_result(
    *,
    extraction: ExtractionResult,
    structural: StructuralScanResult,
    promptguard: PromptGuardResult,
    trust_tier: TrustTier,
    extract_mode: str,
    content_type: str,
    sanitizer_revision: str = "",
    domain_changed_on_redirect: bool = False,
) -> SanitizationResult:
    """Build source-neutral sanitized output from completed stages 1-3.

    Route-specific builders add URL or upload provenance after this function,
    keeping their public response schemas independent.
    """
    trust_score = _compute_trust_score(
        tier=trust_tier,
        stage2_penalty=structural.penalty,
        stage3_penalty=promptguard.penalty,
        domain_changed_on_redirect=domain_changed_on_redirect,
    )

    truncation_notice: str | None = None
    if extract_mode == "summary":
        body, notice = extract_summary(
            extraction.main_content,
            extraction.raw_text,
            extraction.title,
        )
        truncation_notice = notice if notice else None
    else:
        body = extraction.main_content

    result = SanitizationResult(
        title=extraction.title,
        body=body,
        word_count=len(body.split()) if body else 0,
        content_type=content_type,
        trust_score=trust_score,
        trust_tier=trust_tier,
        injection_detected=(promptguard.verdict == Stage3Verdict.INJECTION_DETECTED),
        injection_spans=list(promptguard.flagged_chunks),
        structural_flags=[flag.category for flag in structural.flags],
        stage2_verdict=structural.verdict,
        stage3_verdict=promptguard.verdict,
        promptguard_state=_derive_promptguard_state(
            promptguard=promptguard,
            stage2_verdict=structural.verdict,
        ),
        truncation_notice=truncation_notice,
        sanitizer_revision=sanitizer_revision,
    )
    return finalize_quarantine(result)


def finalize_quarantine(result: SanitizationResult) -> SanitizationResult:
    """Remove untrusted content from a blocked sanitization result.

    Structural matches and PromptGuard chunks can contain hostile document text,
    so a quarantine response exposes only stable diagnostic labels.
    """
    structural_blocked = result.stage2_verdict == Stage2Verdict.BLOCKED
    promptguard_blocked = result.stage3_verdict == Stage3Verdict.INJECTION_DETECTED
    if not structural_blocked and not promptguard_blocked:
        return result

    if structural_blocked:
        diagnostic = DIAG_STRUCTURAL_BLOCKED
    elif result.promptguard_state == "unavailable_blocked":
        diagnostic = DIAG_PROMPTGUARD_UNAVAILABLE
    else:
        diagnostic = DIAG_INJECTION_DETECTED
    return SanitizationResult(
        title=result.title,
        body=_QUARANTINE_BODY,
        word_count=len(_QUARANTINE_BODY.split()),
        content_type=result.content_type,
        trust_score=result.trust_score,
        trust_tier=result.trust_tier,
        injection_detected=True,
        injection_spans=[diagnostic],
        structural_flags=result.structural_flags,
        stage2_verdict=result.stage2_verdict,
        stage3_verdict=result.stage3_verdict,
        promptguard_state=result.promptguard_state,
        truncation_notice=None,
        sanitizer_revision=result.sanitizer_revision,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_retrieved_content(
    *,
    request_id: str,
    source_url: str,
    final_url: str,
    domain: str,
    sanitization: SanitizationResult,
    redirect_chain: list[str],
    domain_changed_on_redirect: bool,
    cache_hit: bool = False,
    cached_at: datetime | None = None,
) -> RetrievedContent:
    """Assemble a :class:`RetrievedContent` from pipeline stage results.

    Parameters
    ----------
    request_id:
        UUID for this retrieval.
    source_url:
        Original URL requested.
    final_url:
        URL after redirects resolved.
    domain:
        Domain of *final_url*.
    sanitization:
        Completed source-neutral Stage 2-4 result. Its trust tier was resolved
        before PromptGuard ran and is therefore never re-derived here.
    redirect_chain:
        Ordered list of redirect URLs.
    domain_changed_on_redirect:
        Whether the domain changed between *source_url* and *final_url*.
    cache_hit:
        Whether this result came from cache.
    cached_at:
        When the result was cached (if *cache_hit*).

    Returns
    -------
    RetrievedContent fully populated from pipeline results.
    """
    return RetrievedContent(
        request_id=request_id,
        source_url=source_url,
        final_url=final_url,
        cache_hit=cache_hit,
        cached_at=cached_at,
        title=sanitization.title,
        body=sanitization.body,
        word_count=sanitization.word_count,
        content_type=sanitization.content_type,
        trust_score=sanitization.trust_score,
        trust_tier=sanitization.trust_tier,
        injection_detected=sanitization.injection_detected,
        injection_spans=sanitization.injection_spans,
        structural_flags=sanitization.structural_flags,
        stage2_verdict=sanitization.stage2_verdict,
        stage3_verdict=sanitization.stage3_verdict,
        promptguard_state=sanitization.promptguard_state,
        domain=domain,
        redirect_chain=redirect_chain,
        domain_changed_on_redirect=domain_changed_on_redirect,
        truncation_notice=sanitization.truncation_notice,
    )


def build_extracted_content(
    *,
    request_id: str,
    provenance: UploadProvenance,
    sanitization: SanitizationResult,
) -> ExtractedContent:
    """Assemble the upload-only response from the shared Stage 4 result."""
    return ExtractedContent(
        request_id=request_id,
        title=sanitization.title,
        body=sanitization.body,
        word_count=sanitization.word_count,
        content_type=sanitization.content_type,
        trust_score=sanitization.trust_score,
        trust_tier=sanitization.trust_tier,
        injection_detected=sanitization.injection_detected,
        injection_spans=sanitization.injection_spans,
        structural_flags=sanitization.structural_flags,
        stage2_verdict=sanitization.stage2_verdict,
        stage3_verdict=sanitization.stage3_verdict,
        promptguard_state=sanitization.promptguard_state,
        provenance=provenance,
        truncation_notice=sanitization.truncation_notice,
        sanitizer_revision=sanitization.sanitizer_revision,
    )
