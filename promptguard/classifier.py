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
from typing import TYPE_CHECKING, Any, NoReturn, cast

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
DEFAULT_CUDA_BATCH_SIZE = 16


@dataclass(frozen=True)
class DeviceState:
    """A consistent snapshot of the classifier's device state."""

    device: str
    requested_device: str
    failed_over: bool
    failover_reason: str | None
    oom_refused: bool
    fp32_precision: str | None
    effective_batch_size: int
    device_failovers: int = 0
    oom_batch_reductions: int = 0
    oom_refusals: int = 0


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


class PromptGuardCudaBatchSizeConfigurationError(ValueError):
    """Raised when the configured CUDA batch size is invalid."""


def promptguard_cuda_batch_size_from_config(config: dict[str, Any]) -> int:
    """Read the windows-per-forward-pass cap; only the CUDA path uses it."""
    return bounded_int(
        config,
        "promptguard_cuda_batch_size",
        DEFAULT_CUDA_BATCH_SIZE,
        minimum=1,
        maximum=64,
        error=PromptGuardCudaBatchSizeConfigurationError,
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
        # Per instance, never module-global: the parity tool configures its own.
        self._effective_batch = DEFAULT_CUDA_BATCH_SIZE
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
        # Serialises OOM failovers only: readers never take it, and the CPU
        # copy is built under it but outside ``_state_lock``.
        self._failover_lock = threading.Lock()
        self._oom_batch_reductions = 0
        self._device_failovers = 0
        self._oom_refusals = 0
        # Prompt Guard 2 is binary; the three-class model was Prompt Guard 1.
        # load() verifies the labels and replaces this default from the config.
        self._injection_label_index = 1

    def configure_threads(self, threads: int) -> None:
        """Retain the validated boot setting for every load attempt."""
        self._threads = threads

    def configure_batch_size(self, batch_size: int) -> None:
        """Set this instance's CUDA batch size; callable any number of times."""
        with self._state_lock:
            self._effective_batch = batch_size

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

    @property
    def oom_batch_reductions(self) -> int:
        return self._oom_batch_reductions

    @property
    def device_failovers(self) -> int:
        return self._device_failovers

    @property
    def oom_refusals(self) -> int:
        return self._oom_refusals

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
                effective_batch_size=self._effective_batch,
                device_failovers=self._device_failovers,
                oom_batch_reductions=self._oom_batch_reductions,
                oom_refusals=self._oom_refusals,
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
        chunks = self._chunk_text(text)
        if max_chunks is not None and len(chunks) > max_chunks:
            raise PromptGuardBudgetExceededError(
                "PromptGuard classification input exceeds the chunk budget"
            )
        if device == "cuda":
            return self._classify_cuda(active, tokenizer, chunks), chunks
        return self._score_serial(model, tokenizer, device, chunks), chunks

    def _score_serial(
        self,
        model: PreTrainedModel,
        tokenizer: PreTrainedTokenizerBase,
        device: str,
        chunks: list[str],
    ) -> list[float]:
        """Score *chunks* one window per forward pass (the CPU path)."""
        import torch

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
        return scores

    # -----------------------------------------------------------------
    # GPU out-of-memory handling
    # -----------------------------------------------------------------

    def _classify_cuda(
        self,
        snapshot: tuple[PreTrainedModel, str],
        tokenizer: PreTrainedTokenizerBase,
        chunks: list[str],
    ) -> list[float]:
        """Score *chunks* from a cuda snapshot, surviving OOM and a swap.

        Every pass works on one snapshot and never blocks another reader. The
        windows a pass finished are kept; the next pass scores only the rest.

        After a failed pass, in this order:

        - swapped (``_active`` is no longer *snapshot*) and no swap retry yet:
          retry once on the current snapshot, whatever the failure was
        - a ``RuntimeError`` that is not an OOM: propagate
        - an OOM after the one swap retry: propagate as an OOM (no halving and
          no failover on a model this call no longer owns)
        - an OOM at batch > 1: halve the shared batch and retry
        - an OOM at batch 1: fail over to a CPU copy, or refuse

        A swap is detected by snapshot identity, never by exception text, so a
        reworded torch message cannot turn the retry into a raw error. Logs
        carry closed tokens only, never exception text.
        """
        import torch

        scores: list[float] = []
        used = self._effective_batch
        swap_retried = False
        while True:
            failure: RuntimeError | None = None
            try:
                scores.extend(
                    self._score_pass(snapshot, tokenizer, chunks[len(scores) :], used)
                )
                return scores
            except _CudaOutOfMemoryError as oom:
                scores.extend(oom.done)
                with contextlib.suppress(Exception):
                    torch.cuda.empty_cache()
            except RuntimeError as exc:
                failure = exc

            current = self._swapped_from(snapshot)
            if current is not None and not swap_retried:
                swap_retried = True
                snapshot = current
                continue
            if failure is not None:
                raise failure
            if current is not None:
                raise torch.cuda.OutOfMemoryError("CUDA out of memory") from None
            if used > 1:
                used = self._adopt_smaller_batch(used)
                continue
            snapshot = self._failover_or_refuse(snapshot)

    def _score_pass(
        self,
        snapshot: tuple[PreTrainedModel, str],
        tokenizer: PreTrainedTokenizerBase,
        chunks: list[str],
        batch_size: int,
    ) -> list[float]:
        """One scoring pass on *snapshot*: batched on cuda, serial otherwise."""
        model, device = snapshot
        if device != "cuda":
            return self._score_serial(model, tokenizer, device, chunks)
        scores = self._score_batched(model, tokenizer, device, chunks, batch_size)
        self._clear_oom_refused()
        return scores

    def _swapped_from(
        self, snapshot: tuple[PreTrainedModel, str]
    ) -> tuple[PreTrainedModel, str] | None:
        """The current snapshot if it replaced *snapshot*, else ``None``."""
        current = self._active
        if current is None or current is snapshot:
            return None
        return current

    def _adopt_smaller_batch(self, used: int) -> int:
        """Halve the shared batch once per OOM event; others adopt the result."""
        with self._state_lock:
            if self._effective_batch == used and used > 1:
                self._effective_batch = max(1, used // 2)
                self._oom_batch_reductions += 1
                logger.warning(
                    "promptguard_oom_batch_reduced batch=%d", self._effective_batch
                )
            return self._effective_batch

    def _clear_oom_refused(self) -> None:
        if self._oom_refused:
            with self._state_lock:
                self._oom_refused = False

    def _failover_or_refuse(
        self, snapshot: tuple[PreTrainedModel, str]
    ) -> tuple[PreTrainedModel, str]:
        """At batch 1: swap in a CPU copy (fallback ``cpu``) or refuse."""
        if self._fallback != "cpu":
            self._refuse("oom")
        with self._failover_lock:
            current = self._active
            if current is not snapshot:
                # A concurrent failover already swapped; use whatever is current.
                if current is None:
                    self._refuse("oom")
                return current
            model = snapshot[0]
            try:
                cpu_model = self._build_cpu_copy(model)
            except Exception:
                logger.warning("promptguard_oom_refused reason=copy_failed")
                self._count_refusal()
                raise PromptGuardUnavailableError(
                    "PromptGuard unavailable: GPU out of memory"
                ) from None
            swapped = (cpu_model, "cpu")
            with self._state_lock:
                self._active = swapped
                self._failed_over = True
                self._failover_reason = "oom"
                self._device_failovers += 1
        logger.warning("promptguard_device_failover reason=oom")
        return swapped

    @staticmethod
    def _build_cpu_copy(model: PreTrainedModel) -> PreTrainedModel:
        """Build a CPU model from host-side tensors; never clone on the GPU."""
        source = cast(Any, model)
        cpu_model = type(source)(source.config)
        cpu_model.load_state_dict(
            {k: v.detach().to("cpu") for k, v in source.state_dict().items()}
        )
        cpu_model.eval()
        return cast("PreTrainedModel", cpu_model)

    def _count_refusal(self) -> None:
        with self._state_lock:
            self._oom_refused = True
            self._oom_refusals += 1

    def _refuse(self, reason: str) -> NoReturn:
        logger.warning("promptguard_oom_refused reason=%s", reason)
        self._count_refusal()
        raise PromptGuardUnavailableError("PromptGuard unavailable: GPU out of memory")

    def _score_batched(
        self,
        model: PreTrainedModel,
        tokenizer: PreTrainedTokenizerBase,
        device: str,
        chunks: list[str],
        batch_size: int,
    ) -> list[float]:
        """Score *chunks* in order, at most *batch_size* windows per forward pass.

        Production takes this path on ``cuda`` only; the batch size is a
        parameter so a CPU test can force it against the batch-1 loop.
        """
        import torch

        if not chunks:
            return []
        scores: list[float] = []
        try:
            with self._tokenizer_lock:
                inputs = tokenizer(
                    chunks,
                    return_tensors="pt",
                    truncation=True,
                    max_length=MAX_SEQ_LEN,
                    padding=True,
                )
            if device != "cpu":
                # Token ids are small; move the page once and slice on the device.
                inputs = inputs.to(device)
            for start in range(0, len(chunks), batch_size):
                batch = {
                    key: value[start : start + batch_size]
                    for key, value in inputs.items()
                }
                with torch.no_grad():
                    outputs = model(**batch)
                probs = torch.softmax(outputs.logits, dim=-1)
                # torch types tolist() as list[Unknown]; a 1-D column is floats.
                column = cast(Any, probs[:, self._injection_label_index])
                scores.extend(cast(list[float], column.tolist()))
        except torch.cuda.OutOfMemoryError:
            # Hand back the finished windows so the retry scores only the rest.
            raise _CudaOutOfMemoryError(scores) from None
        return scores


class PromptGuardUnavailableError(RuntimeError):
    """Raised when the GPU ran out of memory and policy forbids scoring on CPU.

    The caller maps it to the tier's ``unavailable_result``; the body is
    unscanned and must never be cached.
    """


class _CudaOutOfMemoryError(Exception):
    """Internal: an OOM carrying the scores completed before it."""

    def __init__(self, done: list[float]) -> None:
        super().__init__()
        self.done = done


class PromptGuardBudgetExceededError(ValueError):
    """Raised instead of silently classifying only a prefix of a document."""


def device_snapshot(classifier: object) -> DeviceState | None:
    """The classifier's device state, or ``None`` when it has none to report.

    Tolerant on purpose: stub and mock classifiers (every attribute exists on a
    ``MagicMock``) and the corpus replay classifier carry no real device, and
    the ``isinstance`` check keeps a mock's fabricated value out of ``/health``
    and out of the content-cache key.
    """
    snapshot = getattr(classifier, "device_state", None)
    if not callable(snapshot) or getattr(classifier, "loaded", False) is not True:
        return None
    result = snapshot()
    return result if isinstance(result, DeviceState) else None
