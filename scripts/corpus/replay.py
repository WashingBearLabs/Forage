"""A recorded-score stand-in for Prompt Guard, at the exact seam stage 3 calls.

Stage 3 calls ``classifier.classify_windows(text, *, max_chunks)`` and nothing
else, so this double sits *below* ``run_promptguard``: the real threshold,
contiguity and trust-tier rules run over replayed scores. It answers from a
table keyed by the SHA-256 of the text and refuses, loudly, to invent a score
for a text it has no entry for.

Nothing here carries text. An unrecorded text is reported as a hash and a
length, and the call log keeps hashes and scores only.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from promptguard.classifier import PromptGuardBudgetExceededError


def text_sha256(text: str) -> str:
    """The cassette key of ``text``: the hex SHA-256 of its UTF-8 bytes."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class UnrecordedTextError(Exception):
    """The classifier was asked about a text the cassette has no scores for.

    Carries only what a classifier can know — the hash and the length. The
    driver re-raises it with the record, route and configuration attached.
    """

    def __init__(self, sha256_hex: str, chars: int) -> None:
        super().__init__(sha256_hex, chars)
        self.sha256_hex = sha256_hex
        self.chars = chars

    def __str__(self) -> str:
        return (
            f"no recorded scores for sha256:{self.sha256_hex[:8]} ({self.chars} chars)"
        )


@dataclass(frozen=True, slots=True)
class ReplayCall:
    """One answered ``classify_windows`` call: a hash and the scores returned."""

    sha256: str
    chars: int
    scores: tuple[float, ...]
    recorded: bool
    """``False`` when the test-only ``fallback`` answered rather than the table."""


class ReplayClassifier:
    """Answers ``classify_windows`` from recorded per-window scores.

    ``scores`` maps ``sha256(text)`` to the per-window scores in document
    order. ``model_id`` and ``revision`` name the classifier the scores were
    recorded from; they are what ``RouteResult.model_id`` reports, never the
    model the app under test booted with.

    ``fallback`` is **test-only**: a constant single-window score returned for
    a text with no entry, so a spec 1-3 test can drive a record with no
    cassette at all (``fallback=0.0`` is the structural-only measurement). The
    CI gate (spec 5) constructs this class **without** it, so a cassette miss
    there is a hard error and never a default score.

    ``calls`` logs every answered call — hash, length and scores, never the
    text — so a driver can read back what a request replayed. It is unbounded
    by design: one classifier serves one drive at a time.
    """

    def __init__(
        self,
        scores: Mapping[str, Sequence[float]],
        *,
        model_id: str,
        revision: str,
        fallback: float | None = None,
    ) -> None:
        self._scores = {sha: tuple(values) for sha, values in scores.items()}
        self.model_id = model_id
        self.revision = revision
        self.fallback = fallback
        self.calls: list[ReplayCall] = []

    @property
    def loaded(self) -> bool:
        """A replay classifier is always ready; there is no model to warm."""
        return True

    def classify_windows(
        self,
        text: str,
        *,
        max_chunks: int | None = None,
    ) -> tuple[list[float], list[str]]:
        """Return ``(scores, chunks)`` in document order, as the real one does.

        Chunk labels are ``window-<i>`` placeholders: the text of a window is
        not recorded, and a placeholder can never smuggle payload into a
        response. Raises ``UnrecordedTextError`` on a miss with no ``fallback``
        and ``PromptGuardBudgetExceededError`` when the recorded window count
        exceeds ``max_chunks`` — the same class, at the same point, as
        ``PromptGuardClassifier.classify_windows``.
        """
        sha = text_sha256(text)
        recorded = sha in self._scores
        if recorded:
            scores = self._scores[sha]
        elif self.fallback is not None:
            scores = (self.fallback,)
        else:
            raise UnrecordedTextError(sha, len(text))
        if max_chunks is not None and len(scores) > max_chunks:
            raise PromptGuardBudgetExceededError(
                "PromptGuard classification input exceeds the chunk budget"
            )
        self.calls.append(ReplayCall(sha, len(text), scores, recorded))
        return list(scores), [f"window-{index}" for index in range(len(scores))]

    def classify(
        self,
        text: str,
        *,
        max_chunks: int | None = None,
    ) -> tuple[float, list[str]]:
        """Return ``(max_score, flagged_chunks)``, pooled as the real ``classify()``."""
        scores, chunks = self.classify_windows(text, max_chunks=max_chunks)
        if not scores:
            return 0.0, []

        max_score = max(scores)
        flagged = [chunks[i] for i, s in enumerate(scores) if s == max_score]

        return max_score, flagged
