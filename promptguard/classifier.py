"""PromptGuard 2 22M classifier — model loading, inference, chunking.

Wraps Meta's Prompt-Guard-2-22M (DeBERTa-v3-base sequence classifier)
for prompt-injection detection.  Runs on CPU only.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Import-time only: torch and transformers are heavyweight and optional at
    # runtime (the classifier degrades to "unavailable" without them), so the
    # real imports stay inside load()/classify(). See typings/transformers for
    # the stub that makes the auto-class factories return something knowable.
    from transformers import PreTrainedModel, PreTrainedTokenizerBase

logger = logging.getLogger(__name__)

MODEL_ID = "meta-llama/Llama-Prompt-Guard-2-22M"
MAX_SEQ_LEN = 512
CHUNK_OVERLAP = 64
MAX_PROMPTGUARD_CHUNKS = 64
# Prompt-Guard-2-22M has 2 output classes: BENIGN (0) and INJECTION (1).
# (The older 86M model had 3 classes with INDIRECT at index 1.)
_INJECTION_LABEL_INDEX = 1


class PromptGuardClassifier:
    """Wraps PromptGuard 2 22M for injection detection.

    Call :meth:`load` once at startup, then :meth:`classify` per request.
    Degrades gracefully — if the model is unavailable, ``loaded`` stays
    ``False`` and :meth:`classify` returns ``(0.0, [])``.
    """

    def __init__(self) -> None:
        self._model: PreTrainedModel | None = None
        self._tokenizer: PreTrainedTokenizerBase | None = None
        self._loaded: bool = False

    @property
    def loaded(self) -> bool:
        """Whether the model is ready for inference."""
        return self._loaded

    def load(self) -> bool:
        """Load model from local HuggingFace cache.

        Returns ``True`` on success, ``False`` if the model or its
        dependencies are not available (torch / transformers missing,
        model not downloaded, etc.).
        """
        try:
            import torch
            from transformers import (
                AutoModelForSequenceClassification,
                AutoTokenizer,
            )

            # torch is imported here so a missing or broken install fails
            # inside this try — at startup — rather than on the first
            # classify() call in a request path. Read the version so the
            # import is not a dead name that a linter would strip.
            logger.debug("PromptGuard loading against torch %s", torch.__version__)

            self._tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
            # `use_safetensors=True` is the loader half of the supply-chain
            # closure `model_fetcher.verify_weights()` opens: without it
            # `from_pretrained` falls back to a pickle checkpoint
            # (`pytorch_model.bin`) and unpickling is arbitrary code
            # execution. The manifest's format allowlist means such a file
            # never reaches the cache — this makes the loader refuse it even
            # if verification were bypassed. Pinned by
            # tests/test_model_fetcher.py.
            model = AutoModelForSequenceClassification.from_pretrained(
                MODEL_ID,
                use_safetensors=True,
            )
            model.eval()
            self._model = model
            self._loaded = True
            logger.info("PromptGuard 2 model loaded successfully")
            return True
        except Exception:
            logger.warning(
                "PromptGuard model not available — ML injection detection disabled",
                exc_info=True,
            )
            self._loaded = False
            return False

    # -----------------------------------------------------------------
    # Chunking
    # -----------------------------------------------------------------

    def _chunk_text(self, text: str) -> list[str]:
        """Split *text* into overlapping token-window chunks.

        Each chunk is at most ``MAX_SEQ_LEN`` tokens.  Overlap is
        ``CHUNK_OVERLAP`` tokens so boundary-spanning injections are
        not missed.  Returns the decoded text for each chunk.
        """
        tokenizer = self._tokenizer
        if tokenizer is None:
            return [text]

        token_ids = tokenizer.encode(text, add_special_tokens=False)

        if len(token_ids) <= MAX_SEQ_LEN:
            return [text]

        chunks: list[str] = []
        step = MAX_SEQ_LEN - CHUNK_OVERLAP
        for start in range(0, len(token_ids), step):
            window = token_ids[start : start + MAX_SEQ_LEN]
            chunk_text = tokenizer.decode(window, skip_special_tokens=True)
            chunks.append(chunk_text)
            # Stop if we've consumed all tokens
            if start + MAX_SEQ_LEN >= len(token_ids):
                break

        return chunks

    # -----------------------------------------------------------------
    # Inference
    # -----------------------------------------------------------------

    def classify(
        self,
        text: str,
        *,
        max_chunks: int | None = None,
    ) -> tuple[float, list[str]]:
        """Return ``(max_score, flagged_chunks)``.

        *max_score* is the highest injection probability (0.0-1.0)
        across all chunks.  *flagged_chunks* contains the text of
        chunk(s) whose score equals the max.

        If the model is not loaded, returns ``(0.0, [])``.
        """
        model = self._model
        tokenizer = self._tokenizer
        if not self._loaded or model is None or tokenizer is None:
            logger.warning(
                "classify() called but model not loaded — returning safe fallback"
            )
            return 0.0, []

        import torch

        chunks = self._chunk_text(text)
        if max_chunks is not None and len(chunks) > max_chunks:
            raise PromptGuardBudgetExceededError(
                "PromptGuard classification input exceeds the chunk budget"
            )
        scores: list[float] = []

        for chunk in chunks:
            inputs = tokenizer(
                chunk,
                return_tensors="pt",
                truncation=True,
                max_length=MAX_SEQ_LEN,
                padding=True,
            )
            with torch.no_grad():
                outputs = model(**inputs)

            # Softmax over logits → probability of injection class
            probs = torch.softmax(outputs.logits, dim=-1)
            injection_prob = float(probs[0, _INJECTION_LABEL_INDEX].item())
            scores.append(injection_prob)

        if not scores:
            return 0.0, []

        max_score = max(scores)
        flagged = [chunks[i] for i, s in enumerate(scores) if s == max_score]

        return max_score, flagged


class PromptGuardBudgetExceededError(ValueError):
    """Raised instead of silently classifying only a prefix of a document."""
