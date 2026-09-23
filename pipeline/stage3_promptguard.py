"""Stage 3 -- PromptGuard 2 ML classification for prompt-injection.

Runs AFTER Stage 2 (structural regex scan).  Uses the raw text from
Stage 1 extraction — NOT Stage 2's annotated output.

When the domain is TRUSTED or the model is unavailable, the stage is
skipped and a safe fallback is returned.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator, Coroutine
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from models import Stage3Verdict, TrustTier

if TYPE_CHECKING:
    from promptguard.classifier import PromptGuardClassifier

logger = logging.getLogger(__name__)

DEFAULT_THRESHOLD = 0.85
INJECTION_PENALTY = -0.5

SkipReason = Literal["trusted_tier", "model_unavailable", "structural_block"]


class PromptGuardConfigurationError(ValueError):
    """Raised when a contiguity setting is outside its supported bounds."""


@dataclass(frozen=True, slots=True)
class PromptGuardSettings:
    """Server-only contiguity policy, validated once at process start."""

    contiguity_windows: int = 0
    contiguity_threshold: float = 0.5


def promptguard_settings_from_config(config: dict[str, Any]) -> PromptGuardSettings:
    """Read bounded settings without accepting bools or echoing invalid values."""
    windows = config.get("promptguard_contiguity_windows", 0)
    if (
        isinstance(windows, bool)
        or not isinstance(windows, int)
        or windows not in (0, *range(2, 9))
    ):
        raise PromptGuardConfigurationError(
            "promptguard_contiguity_windows must be 0 or an integer between 2 and 8"
        )
    threshold = config.get("promptguard_contiguity_threshold", 0.5)
    if (
        isinstance(threshold, bool)
        or not isinstance(threshold, int | float)
        or not 0.0 <= threshold <= 1.0
    ):
        raise PromptGuardConfigurationError(
            "promptguard_contiguity_threshold must be a number between 0.0 and 1.0"
        )
    return PromptGuardSettings(windows, float(threshold))


@asynccontextmanager
async def completed_thread[T](
    work: Coroutine[Any, Any, T],
) -> AsyncGenerator[asyncio.Task[T]]:
    """Drain owned thread work before allowing its caller to release a gate.

    Cancelling a to_thread await cannot stop its thread. Keep its task intact
    through repeated cancellation, then let the caller retrieve/map its outcome
    synchronously (including the fetched-PDF spool warning). Pending cancellation
    wins only after that outcome is observed. Queued gate acquisition is outside
    this scope and remains promptly cancellable.
    """
    worker = asyncio.create_task(work)
    cancellation: asyncio.CancelledError | None = None
    while not worker.done():
        try:
            await asyncio.wait({worker})
        except asyncio.CancelledError as exc:
            if cancellation is None:
                cancellation = exc
    try:
        yield worker
    finally:
        if cancellation is not None:
            raise cancellation


@dataclass(frozen=True, slots=True)
class PromptGuardResult:
    """Result of the Stage 3 PromptGuard classification."""

    verdict: Stage3Verdict
    score: float
    flagged_chunks: list[str] = field(default_factory=list[str])
    penalty: float = 0.0
    skipped: bool = False
    skip_reason: SkipReason | None = None
    rule: Literal["max_score", "contiguity", "both"] | None = None


def unavailable_result(
    tier_value: str,
    *,
    fail_closed: bool,
) -> PromptGuardResult:
    """Return the classifier-unavailable Stage 3 result for *tier_value*.

    Pure — it logs nothing. Two callers produce this result and each owns its
    own WARNING: :func:`run_promptguard` below, when the classifier is absent
    or still warming, and ``pipeline/orchestrator.py``'s classification-wait
    timeout, whose operator signal is ``classification_wait_timeout`` because
    the classifier there is *loaded and busy*, not missing.

    Every value it returns — the verdict, the literal flagged chunk, the
    penalty and the ``model_unavailable`` skip reason — reaches the wire
    through stage 4 (``promptguard_state`` ``unavailable_blocked`` /
    ``unavailable_allowed``), so restating them at the timeout site would be a
    second copy free to drift from this one.
    """
    if fail_closed and tier_value in (
        TrustTier.STANDARD.value,
        TrustTier.UNTRUSTED.value,
    ):
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
    return PromptGuardResult(
        verdict=Stage3Verdict.SAFE,
        score=0.0,
        flagged_chunks=[],
        penalty=-0.1,
        skipped=True,
        skip_reason="model_unavailable",
    )


async def run_promptguard(
    text: str,
    classifier: PromptGuardClassifier | None,
    threshold: float = DEFAULT_THRESHOLD,
    trust_tier: TrustTier | str = "standard",
    fail_closed: bool = True,
    max_chunks: int | None = None,
    *,
    contiguity_windows: int = 0,
    contiguity_threshold: float = 0.5,
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
        Max-score threshold (0.0-1.0), strictly exceeded to fire.
    contiguity_windows:
        Zero disables the run rule; otherwise at least this many adjacent
        windows must reach the absolute server-side ``contiguity_threshold``.
        The two rules are independent: either can block.
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
        else:
            logger.warning(
                "PromptGuard unavailable — %s for %s tier",
                (
                    "lenient fallback"
                    if tier_value == TrustTier.VERIFIED.value
                    else "fail-open"
                ),
                tier_value,
            )
        return unavailable_result(tier_value, fail_closed=fail_closed)

    # Run synchronous PyTorch inference in a thread to avoid blocking
    # the event loop.
    async with completed_thread(
        asyncio.to_thread(classifier.classify_windows, text, max_chunks=max_chunks)
    ) as inference:
        scores, chunks = inference.result()

    score = max(scores, default=0.0)
    max_fired = score > threshold
    contiguous: set[int] = set()
    longest_run = 0
    if contiguity_windows:
        start = 0
        # The final boundary flushes a run ending at the last window.
        for end in range(len(scores) + 1):
            if end < len(scores) and scores[end] >= contiguity_threshold:
                continue
            length = end - start
            if length >= contiguity_windows:
                contiguous.update(range(start, end))
                longest_run = max(longest_run, length)
            start = end + 1

    if contiguous:
        logger.warning(
            "promptguard_contiguity_verdict — run=%d windows=%d",
            longest_run,
            len(scores),
        )

    if max_fired or contiguous:
        return PromptGuardResult(
            verdict=Stage3Verdict.INJECTION_DETECTED,
            score=score,
            flagged_chunks=[
                chunks[index]
                for index, value in enumerate(scores)
                if (max_fired and value == score) or index in contiguous
            ],
            penalty=INJECTION_PENALTY,
            skipped=False,
            rule=(
                "both"
                if max_fired and contiguous
                else "max_score"
                if max_fired
                else "contiguity"
            ),
        )

    return PromptGuardResult(
        verdict=Stage3Verdict.SAFE,
        score=score,
        flagged_chunks=[],
        penalty=0.0,
        skipped=False,
    )
