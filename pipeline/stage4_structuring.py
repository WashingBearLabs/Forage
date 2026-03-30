"""Stage 4 -- Content structuring and trust score computation.

Assembles the final :class:`RetrievedContent` object from the outputs of
Stages 1-3 plus redirect/domain metadata.  Computes a composite trust
score and resolves the domain trust tier.

Summary mode uses :func:`extract_summary` from the smart extraction
module to preserve high-signal content (statistics, quotes, references)
while trimming filler.
"""

from __future__ import annotations

from datetime import UTC, datetime

from models import (
    RetrievedContent,
    Stage2Verdict,
    Stage3Verdict,
    TrustTier,
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


# ---------------------------------------------------------------------------
# Trust tier resolution
# ---------------------------------------------------------------------------


def _resolve_trust_tier(
    domain: str,
    trusted_domains: list[str],
    verified_domains: list[str],
    blocked_domains: list[str],
) -> TrustTier:
    """Resolve the trust tier for *domain* from the provided lists.

    Lookup order: blocked -> trusted -> verified -> STANDARD.
    """
    if domain in blocked_domains:
        return TrustTier.BLOCKED
    if domain in trusted_domains:
        return TrustTier.TRUSTED
    if domain in verified_domains:
        return TrustTier.VERIFIED
    return TrustTier.STANDARD


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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_retrieved_content(
    *,
    request_id: str,
    source_url: str,
    final_url: str,
    domain: str,
    extract_mode: str,
    extraction: ExtractionResult,
    structural: StructuralScanResult,
    promptguard: PromptGuardResult,
    redirect_chain: list[str],
    domain_changed_on_redirect: bool,
    trusted_domains: list[str],
    verified_domains: list[str],
    blocked_domains: list[str],
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
    extract_mode:
        ``"full"`` or ``"summary"``.
    extraction:
        Stage 1 extraction result.
    structural:
        Stage 2 structural scan result.
    promptguard:
        Stage 3 PromptGuard classification result.
    redirect_chain:
        Ordered list of redirect URLs.
    domain_changed_on_redirect:
        Whether the domain changed between *source_url* and *final_url*.
    trusted_domains:
        Domains to treat as TRUSTED tier.
    verified_domains:
        Domains to treat as VERIFIED tier.
    blocked_domains:
        Domains to treat as BLOCKED tier.
    cache_hit:
        Whether this result came from cache.
    cached_at:
        When the result was cached (if *cache_hit*).

    Returns
    -------
    RetrievedContent fully populated from pipeline results.
    """
    # -- Trust tier & score --
    trust_tier = _resolve_trust_tier(
        domain, trusted_domains, verified_domains, blocked_domains,
    )
    trust_score = _compute_trust_score(
        tier=trust_tier,
        stage2_penalty=structural.penalty,
        stage3_penalty=promptguard.penalty,
        domain_changed_on_redirect=domain_changed_on_redirect,
    )

    # -- Body text --
    truncation_notice: str | None = None
    if extract_mode == "summary":
        body, notice = extract_summary(
            extraction.main_content, extraction.raw_text, extraction.title,
        )
        truncation_notice = notice if notice else None
    else:
        body = extraction.main_content

    # -- Injection spans --
    injection_detected = promptguard.verdict == Stage3Verdict.INJECTION_DETECTED
    injection_spans = list(promptguard.flagged_chunks)

    # -- Structural flags --
    structural_flags = [f.category for f in structural.flags]

    # -- Word count on final body --
    word_count = len(body.split()) if body else 0

    return RetrievedContent(
        request_id=request_id,
        source_url=source_url,
        final_url=final_url,
        cache_hit=cache_hit,
        cached_at=cached_at,
        title=extraction.title,
        body=body,
        word_count=word_count,
        content_type="html",
        trust_score=trust_score,
        trust_tier=trust_tier,
        injection_detected=injection_detected,
        injection_spans=injection_spans,
        structural_flags=structural_flags,
        stage2_verdict=structural.verdict,
        stage3_verdict=promptguard.verdict,
        domain=domain,
        redirect_chain=redirect_chain,
        domain_changed_on_redirect=domain_changed_on_redirect,
        truncation_notice=truncation_notice,
    )
