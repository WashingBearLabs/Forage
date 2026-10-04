"""Window poolers: pure predicates over a text's per-window scores.

Each factory returns a predicate that is true when the pooling rule would
*fire* on a score sequence. The two here mirror live stage 3
(``pipeline/stage3_promptguard.py``) exactly, comparison operators included:
``max_score`` is strict ``>`` (``max_fired = score > threshold``) and
``contiguity`` is inclusive ``>=`` (``scores[end] >= contiguity_threshold``),
because the report recomputes ``PromptGuardResult.rule`` — which is not on the
wire — from the replayed scores and must agree with the rule that fired.
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
