"""Window poolers: pure predicates over a text's per-window scores.

Each factory returns a predicate that is true when the pooling rule would
*fire* on a score sequence. The first two mirror live stage 3
(``pipeline/stage3_promptguard.py``) exactly, comparison operators included:
``max_score`` is strict ``>`` (``max_fired = score > threshold``) and
``contiguity`` is inclusive ``>=`` (``scores[end] >= contiguity_threshold``),
because the report recomputes ``PromptGuardResult.rule`` — which is not on the
wire — from the replayed scores and must agree with the rule that fired.
``live_contiguity`` is the composite the ``contiguity`` rule config actually
ships. The other three (``k_anywhere``, ``mean_aggregate``, ``smoothed``) are
offline-only candidates for the decision table's sweep; all of them are
inclusive ``>=`` — only ``max_score`` is strict.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Final

Pooler = Callable[[Sequence[float]], bool]

LIVE_THRESHOLD: Final = 0.85
"""The shipped ``promptguard_threshold`` (``config.yaml``)."""

LIVE_CONTIGUITY_WINDOWS: Final = 2
"""The contiguity run length the ``contiguity`` rule config boots with."""

LIVE_CONTIGUITY_THRESHOLD: Final = 0.5
"""The contiguity window threshold the ``contiguity`` rule config boots with."""


def max_score(threshold: float) -> Pooler:
    """Fires when the highest window score is strictly above ``threshold``."""

    def fires(scores: Sequence[float]) -> bool:
        return max(scores, default=0.0) > threshold

    return fires


def contiguity(windows: int, threshold: float) -> Pooler:
    """Fires on any run of at least ``windows`` consecutive scores ``>= threshold``."""

    def fires(scores: Sequence[float]) -> bool:
        run = 0
        for score in scores:
            run = run + 1 if score >= threshold else 0
            if run >= windows:
                return True
        return False

    return fires


def live_contiguity() -> Pooler:
    """The shipped ``contiguity`` rule: the max rule OR the contiguity run.

    ``run_promptguard`` fires on ``max_fired or contiguous`` and the
    ``contiguity`` boot keeps the 0.85 max threshold, so a lone window above
    0.85 with no qualifying run is a live catch the bare run rule misses.
    """
    by_max = max_score(LIVE_THRESHOLD)
    by_run = contiguity(LIVE_CONTIGUITY_WINDOWS, LIVE_CONTIGUITY_THRESHOLD)

    def fires(scores: Sequence[float]) -> bool:
        return by_max(scores) or by_run(scores)

    return fires


def k_anywhere(windows: int, threshold: float) -> Pooler:
    """Fires when at least ``windows`` scores are ``>= threshold``, adjacent or not."""

    def fires(scores: Sequence[float]) -> bool:
        return sum(score >= threshold for score in scores) >= windows

    return fires


def mean_aggregate(threshold: float) -> Pooler:
    """Fires when the mean of every window score is ``>= threshold``."""

    def fires(scores: Sequence[float]) -> bool:
        return bool(scores) and sum(scores) / len(scores) >= threshold

    return fires


def smoothed(windows: int, threshold: float) -> Pooler:
    """Fires when any moving average over ``windows`` windows is ``>= threshold``.

    A sequence shorter than ``windows`` is averaged whole, so it degrades to
    ``mean_aggregate`` rather than never firing.
    """

    def fires(scores: Sequence[float]) -> bool:
        if not scores:
            return False
        span = min(windows, len(scores))
        return any(
            sum(scores[start : start + span]) / span >= threshold
            for start in range(len(scores) - span + 1)
        )

    return fires
