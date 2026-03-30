"""Stage 3 -- PromptGuard 2 ML classification for prompt-injection.

Runs AFTER Stage 2 (structural regex scan).  Uses the raw text from
Stage 1 extraction — NOT Stage 2's annotated output.

When the domain is TRUSTED or the model is unavailable, the stage is
skipped and a safe fallback is returned.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from models import Stage3Verdict, TrustTier

if TYPE_CHECKING:
    from promptguard.classifier import PromptGuardClassifier

logger = logging.getLogger(__name__)

DEFAULT_THRESHOLD = 0.85
INJECTION_PENALTY = -0.5


@dataclass(frozen=True, slots=True)
class PromptGuardResult:
    """Result of the Stage 3 PromptGuard classification."""

    verdict: Stage3Verdict
    score: float
    flagged_chunks: list[str] = field(default_factory=list)
    penalty: float = 0.0
    skipped: bool = False


def run_promptguard(
    text: str,
    classifier: PromptGuardClassifier | None,
    threshold: float = DEFAULT_THRESHOLD,
    trust_tier: str = "standard",
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

    Returns
    -------
    PromptGuardResult with verdict, score, flagged chunks, and penalty.
    """
    # TRUSTED domains skip ML classification
    if trust_tier == TrustTier.TRUSTED or trust_tier == TrustTier.TRUSTED.value:
        logger.debug("Skipping PromptGuard for TRUSTED domain")
        return PromptGuardResult(
            verdict=Stage3Verdict.SAFE,
            score=0.0,
            flagged_chunks=[],
            penalty=0.0,
            skipped=True,
        )

    # Model not available — degrade gracefully
    if classifier is None or not classifier.loaded:
        logger.warning(
            "PromptGuard model not loaded — returning safe fallback"
        )
        return PromptGuardResult(
            verdict=Stage3Verdict.SAFE,
            score=0.0,
            flagged_chunks=[],
            penalty=0.0,
            skipped=True,
        )

    score, flagged_chunks = classifier.classify(text)

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
