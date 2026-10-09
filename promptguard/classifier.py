"""PromptGuard 2 classifier — model loading, inference, chunking.

Wraps Meta's Prompt Guard 2 sequence classifiers (22M by default)
for prompt-injection detection.  Runs on CPU by default; ``FORAGE_DEVICE=cuda``
(see :mod:`promptguard.device`) moves the model to the GPU at fp32.
"""

from __future__ import annotations

import contextlib
import logging
import os
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from pipeline.config_bounds import bounded_int
from promptguard.device import DeviceSettings

if TYPE_CHECKING:
    # Import-time only: torch and transformers are heavyweight and optional at
    # runtime (the classifier degrades to "unavailable" without them), so the
    # real imports stay inside load()/classify_windows(). See typings/transformers for
    # the stub that makes the auto-class factories return something knowable.
    from transformers import PreTrainedModel, PreTrainedTokenizerBase

logger = logging.getLogger(__name__)

PROMPT_GUARD_22M_ID = "meta-llama/Llama-Prompt-Guard-2-22M"
PROMPT_GUARD_86M_ID = "meta-llama/Llama-Prompt-Guard-2-86M"
# The 86M since the 2026-10-06 owner ruling: on the injection corpus the 22M's
# stage-3 catch was nil and the 86M's was not, with no benign false positive
# for either (docs/corpus.md "Decision inputs"). The 22M stays allowlisted as
# the smaller opt-out via FORAGE_MODEL_ID.
DEFAULT_MODEL_ID = PROMPT_GUARD_86M_ID
# This verified snapshot omits label names. Meta's Prompt Guard 2 inference
# uses the last binary logit for maliciousness; never assume that for a new pin.
_PINNED_GENERIC_LABEL_INDICES = {
    (PROMPT_GUARD_22M_ID, "11614a155199674a0a95e6602d6ab0417b790ed0"): 1,
    # The 86M snapshot omits label names too; index 1 is pinned on probe evidence
    # (corpus-86m-enablement US-002), not on the 22M's say-so.
    (PROMPT_GUARD_86M_ID, "a8ded8e697ce7c355e395a0df51f94adb4a2fd27"): 1,
}
MAX_SEQ_LEN = 512
CHUNK_OVERLAP = 64
MAX_PROMPTGUARD_CHUNKS = 64


@dataclass(frozen=True)
class DeviceState:
    """A consistent snapshot of the classifier's device state."""

    device: str
    requested_device: str
    failed_over: bool
    failover_reason: str | None
    oom_refused: bool
    fp32_precision: str | None


class PromptGuardThreadsConfigurationError(ValueError):
    """Raised when the configured CPU thread count is invalid."""


def promptguard_threads_from_config(config: dict[str, Any]) -> int:
    """Read the boot thread count; zero leaves both libraries' defaults alone."""
    return bounded_int(
        config,
        "promptguard_threads",
        0,
        minimum=0,
        maximum=16,
        error=PromptGuardThreadsConfigurationError,
    )


class PromptGuardClassifier:
    """Wraps PromptGuard 2 for injection detection.

    Call :meth:`load` once at startup, then :meth:`classify` per request.
    Degrades gracefully — if the model is unavailable, ``loaded`` stays
    ``False`` and :meth:`classify` returns ``(0.0, [])``. Use
    :meth:`classify_windows` for scores and texts in document order.
    """

    def __init__(self) -> None:
        self._tokenizer: PreTrainedTokenizerBase | None = None
        # Fast tokenizers configure shared backend truncation/padding before
        # encoding. Keep each complete tokenizer operation atomic across
        # classification workers, but never hold this lock during inference.
        self._tokenizer_lock = threading.Lock()
        self._loaded: bool = False
        self._threads = 0
        # The (model, device) pair is ONE reference, replaced whole, so a
        # reader that snapshots it can never see a model on one device paired
        # with the name of another. load() is its single swap point.
        self._active: tuple[PreTrainedModel, str] | None = None
        self._requested_device = "cpu"
        self._fallback = "cpu"
        self._boot_probe_failed = False
        self._failed_over = False
        self._failover_reason: str | None = None
        self._oom_refused = False
        self._fp32_precision: str | None = None
        # Latched when a failed CUDA move could not be recovered from: the
        # retried load() must not touch CUDA again.
        self._cuda_unusable = False
        self._state_lock = threading.Lock()
        # Prompt Guard 2 is binary; the three-class model was Prompt Guard 1.
        # load() verifies the labels and replaces this default from the config.
        self._injection_label_index = 1

    def configure_threads(self, threads: int) -> None:
        """Retain the validated boot setting for every load attempt."""
        self._threads = threads

    def configure_device(
        self, settings: DeviceSettings, boot_probe_failed: bool
    ) -> None:
        """Retain the validated device choice and the boot probe verdict."""
        with self._state_lock:
            self._requested_device = settings.device
            self._fallback = settings.fallback
            self._boot_probe_failed = boot_probe_failed

    @property
    def _model(self) -> PreTrainedModel | None:
        active = self._active
        return None if active is None else active[0]

    @_model.setter
    def _model(self, model: PreTrainedModel | None) -> None:
        # Kept for callers that install a model directly (tests): it pairs the
        # model with the device it is already on, i.e. the current one.
        self._active = None if model is None else (model, self.device)

    @property
    def device(self) -> str:
        """The device the active model runs on (``cpu`` before any load)."""
        active = self._active
        return "cpu" if active is None else active[1]

    @property
    def requested_device(self) -> str:
        return self._requested_device

    @property
    def failed_over(self) -> bool:
        return self._failed_over

    @property
    def oom_refused(self) -> bool:
        return self._oom_refused

    def device_state(self) -> DeviceState:
        """A frozen snapshot, read under the lock so it cannot be torn."""
        with self._state_lock:
            return DeviceState(
                device=self.device,
                requested_device=self._requested_device,
                failed_over=self._failed_over,
                failover_reason=self._failover_reason,
                oom_refused=self._oom_refused,
                fp32_precision=self._fp32_precision,
            )

    def _mark_failed_over(self, reason: str) -> None:
        with self._state_lock:
            self._failed_over = True
            self._failover_reason = reason
        logger.warning("promptguard_device_failover reason=%s", reason)

    def _move_to_cuda(self, model: PreTrainedModel) -> tuple[bool, str | None]:
        """Move *model* to CUDA at fp32; on failure restore it to CPU.

        Returns ``(on_cuda, failure_reason)``. The reason is a closed token:
        ``unavailable``, ``oom``, ``load_error`` for a failed move that was
        recovered, or ``recovery_error`` when the model could not be put back
        on CPU (which also latches ``_cuda_unusable``). Exception text is
        never logged or returned.
        """
        import torch

        try:
            # The torch stubs do not declare the newer precision knobs, so the
            # backends are reached through Any; the AttributeError fallback is
            # the pre-2.9 spelling of the same setting.
            backends = cast(Any, torch.backends)
            mode = "fp32_precision"
            try:
                backends.cuda.matmul.fp32_precision = "ieee"
                backends.cudnn.conv.fp32_precision = "ieee"
            except AttributeError:
                mode = "allow_tf32"
                backends.cuda.matmul.allow_tf32 = False
                backends.cudnn.allow_tf32 = False
            with self._state_lock:
                self._fp32_precision = mode
            model.to("cuda")
            return True, None
        except Exception as exc:
            if isinstance(exc, torch.cuda.OutOfMemoryError):
                reason = "oom"
            elif isinstance(exc, (AssertionError, RuntimeError)) and not (
                torch.cuda.is_available()
            ):
                reason = "unavailable"
            else:
                reason = "load_error"
        try:
            model.to("cpu")
            # Best effort; the parameters, not the cache, are what is checked.
            with contextlib.suppress(Exception):
                torch.cuda.empty_cache()
            if any(p.device.type != "cpu" for p in model.parameters()):
                raise RuntimeError("parameters remain off cpu")
        except Exception:
            self._cuda_unusable = True
            return False, "recovery_error"
        return False, reason

    @property
    def loaded(self) -> bool:
        """Whether the model is ready for inference."""
        return self._loaded

    def load(
        self,
        *,
        model_id: str | None = None,
        revision: str | None = None,
        cache_dir: Path | str | None = None,
        local_files_only: bool = False,
    ) -> bool:
        """Load model from the local HuggingFace cache.

        Returns ``True`` on success, ``False`` if the model or its
        dependencies are not available (torch / transformers missing,
        model not downloaded, etc.).

        *model_id* defaults to :data:`DEFAULT_MODEL_ID`, *revision* pins the
        commit sha to open, and *cache_dir* is the **hub**
        cache (``$HF_HOME/hub``) the weights were verified in, and
        *local_files_only* forbids the hub round-trip transformers otherwise
        makes even on a full cache hit. ``model_fetcher.acquire_and_load()``
        supplies the identity and cache after :func:`model_fetcher.verify_weights` has
        blessed the exact file set; the defaults preserve the pre-US-001
        behaviour for any other caller.
        """
        self._loaded = False
        model_id = DEFAULT_MODEL_ID if model_id is None else model_id
        if cache_dir is not None and not Path(cache_dir).is_dir():
            logger.warning("model_cache_dir_missing")
            return False
        model: PreTrainedModel
        tokenizer: PreTrainedTokenizerBase
        try:
            import torch

            if self._threads > 0:
                try:
                    os.environ["TOKENIZERS_PARALLELISM"] = "false"
                    torch.set_num_threads(self._threads)
                except Exception as exc:
                    logger.warning(
                        "promptguard_threads_apply_failed — n=%d error=%s",
                        self._threads,
                        type(exc).__name__,
                    )

            from transformers import (
                AutoModelForSequenceClassification,
                AutoTokenizer,
            )

            # torch is imported here so a missing or broken install fails
            # inside this try — at startup — rather than on the first
            # classify() call in a request path. Read the version so the
            # import is not a dead name that a linter would strip.
            logger.debug("PromptGuard loading against torch %s", torch.__version__)

            tokenizer = AutoTokenizer.from_pretrained(
                model_id,
                revision=revision,
                cache_dir=cache_dir,
                local_files_only=local_files_only,
            )
            # `use_safetensors=True` is the loader half of the supply-chain
            # closure `model_fetcher.verify_weights()` opens: without it
            # `from_pretrained` falls back to a pickle checkpoint
            # (`pytorch_model.bin`) and unpickling is arbitrary code
            # execution. The manifest's format allowlist means such a file
            # never reaches the cache — this makes the loader refuse it even
            # if verification were bypassed. Pinned by
            # tests/test_model_fetcher.py.
            model = AutoModelForSequenceClassification.from_pretrained(
                model_id,
                use_safetensors=True,
                revision=revision,
                cache_dir=cache_dir,
                local_files_only=local_files_only,
            )
        except Exception:
            logger.warning(
                "PromptGuard model not available — ML injection detection disabled",
                exc_info=True,
            )
            self._loaded = False
            return False

        id2label: object = getattr(getattr(model, "config", None), "id2label", None)
        labels: Mapping[object, object] = (
            cast(Mapping[object, object], id2label)
            if isinstance(id2label, Mapping)
            else {}
        )
        ordered_labels = [
            label.upper() if isinstance(label, str) else None
            for label in (labels.get(0), labels.get(1))
        ]
        injection_index: int | None = None
        if len(labels) == 2:
            if set(ordered_labels) == {"BENIGN", "INJECTION"}:
                injection_index = ordered_labels.index("INJECTION")
            elif ordered_labels == ["LABEL_0", "LABEL_1"] and revision is not None:
                injection_index = _PINNED_GENERIC_LABEL_INDICES.get(
                    (model_id, revision)
                )
        if injection_index is None:
            logger.warning("model_labels_unexpected")
            return False

        self._injection_label_index = injection_index
        model.eval()

        device = "cpu"
        failover: str | None = None
        with self._state_lock:
            want_cuda = self._requested_device == "cuda"
            refuse = self._fallback == "refuse"
            probe_failed = self._boot_probe_failed
            unusable = self._cuda_unusable
        if want_cuda and (probe_failed or unusable):
            reason = "unavailable" if probe_failed else "recovery_error"
            if refuse:
                logger.warning("promptguard_device_load_failed reason=%s", reason)
                return False
            failover = "unavailable" if probe_failed else "load_error"
        elif want_cuda:
            on_cuda, reason = self._move_to_cuda(model)
            if on_cuda:
                device = "cuda"
            elif refuse or reason == "recovery_error":
                # Degraded promptguard_unavailable; WeightAcquisition.run()
                # retries on its schedule, which is the intended self-healing.
                logger.warning("promptguard_device_load_failed reason=%s", reason)
                return False
            else:
                failover = reason
        if failover is not None:
            self._mark_failed_over(failover)
        self._tokenizer = tokenizer
        self._active = (model, device)
        self._loaded = True
        logger.info("PromptGuard 2 model loaded successfully")
        return True

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

        with self._tokenizer_lock:
            token_ids = tokenizer.encode(text, add_special_tokens=False)

        if len(token_ids) <= MAX_SEQ_LEN:
            return [text]

        chunks: list[str] = []
        step = MAX_SEQ_LEN - CHUNK_OVERLAP
        for start in range(0, len(token_ids), step):
            window = token_ids[start : start + MAX_SEQ_LEN]
            with self._tokenizer_lock:
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
        scores, chunks = self.classify_windows(text, max_chunks=max_chunks)
        if not scores:
            return 0.0, []

        max_score = max(scores)
        flagged = [chunks[i] for i, s in enumerate(scores) if s == max_score]

        return max_score, flagged

    def classify_windows(
        self,
        text: str,
        *,
        max_chunks: int | None = None,
    ) -> tuple[list[float], list[str]]:
        """Return ``(scores, chunks)`` in document order, one score per chunk.

        Enforce *max_chunks* before inference, never classify only a prefix.
        If the model is not loaded, return ``([], [])`` with a warning.
        """
        active = self._active
        tokenizer = self._tokenizer
        if not self._loaded or active is None or tokenizer is None:
            logger.warning(
                "classify() called but model not loaded — returning safe fallback"
            )
            return [], []

        model, device = active
        import torch

        chunks = self._chunk_text(text)
        if max_chunks is not None and len(chunks) > max_chunks:
            raise PromptGuardBudgetExceededError(
                "PromptGuard classification input exceeds the chunk budget"
            )
        scores: list[float] = []

        for chunk in chunks:
            with self._tokenizer_lock:
                inputs = tokenizer(
                    chunk,
                    return_tensors="pt",
                    truncation=True,
                    max_length=MAX_SEQ_LEN,
                    padding=True,
                )
            if device != "cpu":
                inputs = inputs.to(device)
            with torch.no_grad():
                outputs = model(**inputs)

            # Softmax over logits → probability of injection class
            probs = torch.softmax(outputs.logits, dim=-1)
            injection_prob = float(probs[0, self._injection_label_index].item())
            scores.append(injection_prob)

        return scores, chunks


class PromptGuardBudgetExceededError(ValueError):
    """Raised instead of silently classifying only a prefix of a document."""
