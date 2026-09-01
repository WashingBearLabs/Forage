"""Stage 3 -- PromptGuard 2 ML classification for prompt-injection.

Runs AFTER Stage 2 (structural regex scan).  Uses the raw text from
Stage 1 extraction — NOT Stage 2's annotated output.

When the domain is TRUSTED or the model is unavailable, the stage is
skipped and a safe fallback is returned.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from models import Stage3Verdict, TrustTier

if TYPE_CHECKING:
    from promptguard.classifier import PromptGuardClassifier

logger = logging.getLogger(__name__)

DEFAULT_THRESHOLD = 0.85
INJECTION_PENALTY = -0.5

SkipReason = Literal["trusted_tier", "model_unavailable", "structural_block"]


@dataclass(frozen=True, slots=True)
class PromptGuardResult:
    """Result of the Stage 3 PromptGuard classification."""

    verdict: Stage3Verdict
    score: float
    flagged_chunks: list[str] = field(default_factory=list[str])
    penalty: float = 0.0
    skipped: bool = False
    skip_reason: SkipReason | None = None


async def run_promptguard(
    text: str,
    classifier: PromptGuardClassifier | None,
    threshold: float = DEFAULT_THRESHOLD,
    trust_tier: TrustTier | str = "standard",
    fail_closed: bool = True,
    max_chunks: int | None = None,
) -> PromptGuardResult:
    """Run PromptGuard classification on *text*.

    Parameters
    ----------
    text:
        Raw extracted text from Stage 1.
    classifier:
        The loaded :class:`PromptGuardClassifier`, or ``None`` if
        unavailable.
    threshold:
        Injection confidence threshold (0.0-1.0).  Scores above this
        produce ``INJECTION_DETECTED``.
    trust_tier:
        The resolved trust tier for the domain.  ``"trusted"`` domains
        skip ML classification entirely.
    fail_closed:
        When True (default), block content from STANDARD and UNTRUSTED
        domains if the classifier is unavailable.  When False, allow
        content through with a penalty (fail-open).

    Returns
    -------
    PromptGuardResult with verdict, score, flagged chunks, and penalty.
    """
    # Normalize trust_tier to string value for consistent comparison
    tier_value = trust_tier.value if isinstance(trust_tier, TrustTier) else trust_tier

    # TRUSTED domains skip ML classification
    if tier_value == TrustTier.TRUSTED.value:
        logger.debug("Skipping PromptGuard for TRUSTED domain")
        return PromptGuardResult(
            verdict=Stage3Verdict.SAFE,
            score=0.0,
            flagged_chunks=[],
            penalty=0.0,
            skipped=True,
            skip_reason="trusted_tier",
        )

    # Model not available — behavior depends on fail_closed setting and tier.
    if classifier is None or not classifier.loaded:
        if fail_closed and tier_value in (
            TrustTier.STANDARD.value,
            TrustTier.UNTRUSTED.value,
        ):
            logger.warning(
                "PromptGuard unavailable — fail-closed for %s tier",
                tier_value,
            )
            return PromptGuardResult(
                verdict=Stage3Verdict.INJECTION_DETECTED,
                score=0.0,
                flagged_chunks=[
                    "[PromptGuard unavailable — content blocked as precaution]"
                ],
                penalty=INJECTION_PENALTY,
                skipped=True,
                skip_reason="model_unavailable",
            )
        # Fail-open or VERIFIED tier: degrade gracefully with a penalty.
        logger.warning(
            "PromptGuard unavailable — %s for %s tier",
            (
                "lenient fallback"
                if tier_value == TrustTier.VERIFIED.value
                else "fail-open"
            ),
            tier_value,
        )
        return PromptGuardResult(
            verdict=Stage3Verdict.SAFE,
            score=0.0,
            flagged_chunks=[],
            penalty=-0.1,
            skipped=True,
            skip_reason="model_unavailable",
        )

    # Run synchronous PyTorch inference in a thread to avoid blocking
    # the event loop.
    score, flagged_chunks = await asyncio.to_thread(
        classifier.classify,
        text,
        max_chunks=max_chunks,
    )

    if score > threshold:
        return PromptGuardResult(
            verdict=Stage3Verdict.INJECTION_DETECTED,
            score=score,
            flagged_chunks=flagged_chunks,
            penalty=INJECTION_PENALTY,
            skipped=False,
        )

    return PromptGuardResult(
        verdict=Stage3Verdict.SAFE,
        score=score,
        flagged_chunks=[],
        penalty=0.0,
        skipped=False,
    )
