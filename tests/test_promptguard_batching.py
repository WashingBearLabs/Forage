"""GPU batching of a page's windows (no GPU needed).

The batched path is forced on CPU through ``_score_batched`` and compared with
the batch-1 reference on the tiny seeded model.
"""

from __future__ import annotations

import itertools
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import torch
from fastapi import FastAPI

import retrieval_app
from model_fetcher import WeightAcquisition
from promptguard.classifier import (
    DEFAULT_CUDA_BATCH_SIZE,
    MAX_SEQ_LEN,
    PromptGuardBudgetExceededError,
    PromptGuardClassifier,
    PromptGuardCudaBatchSizeConfigurationError,
    promptguard_cuda_batch_size_from_config,
)
from scripts import promptguard_tiny_model as tiny

# Measured 2026-10-08 (macOS arm64, torch 2.14): max |batched - batch 1| on the
# seeded tiny model was 0.0 at b=4 for every page size below. 1e-5 is the
# spec's tolerance; the real-model drift is measured by the parity tool.
TOLERANCE = 1e-5
BATCH = 4
PAGE_SIZES = (1, BATCH - 1, BATCH, BATCH + 1, 3 * BATCH)


class _CountingModel:
    """Wraps the tiny model and records each forward pass's batch size."""

    def __init__(self) -> None:
        self.model = tiny.build_model()
        self.batch_sizes: list[int] = []

    def __call__(self, **kwargs: torch.Tensor) -> Any:
        self.batch_sizes.append(int(kwargs["input_ids"].shape[0]))
        return self.model(**kwargs)


class _Tokenizer:
    """Maps chunk text to fixed token ids; pads a list with a masked id 0."""

    def __init__(self, by_text: dict[str, list[int]]) -> None:
        self.by_text = by_text
        self.calls: list[dict[str, Any]] = []

    def __call__(self, text: str | list[str], **kwargs: Any) -> dict[str, torch.Tensor]:
        self.calls.append({"text": text, **kwargs})
        texts = [text] if isinstance(text, str) else text
        windows = [self.by_text[t] for t in texts]
        width = max(len(w) for w in windows)
        ids = torch.tensor([w + [0] * (width - len(w)) for w in windows])
        mask = torch.tensor([[1] * len(w) + [0] * (width - len(w)) for w in windows])
        return {"input_ids": ids, "attention_mask": mask}


def _page(size: int) -> tuple[list[str], list[list[int]], _Tokenizer]:
    windows = list(itertools.islice(itertools.cycle(tiny.sample_inputs()), size))
    chunks = [f"w{i}" for i in range(size)]
    return chunks, windows, _Tokenizer(dict(zip(chunks, windows, strict=True)))


def _installed(
    model: object, tokenizer: object, device: str, chunks: list[str]
) -> PromptGuardClassifier:
    classifier = PromptGuardClassifier()
    target = cast(Any, classifier)
    target._active = (model, device)
    target._tokenizer = tokenizer
    target._loaded = True
    target._chunk_text = MagicMock(return_value=chunks)
    return classifier


# -- the knob ---------------------------------------------------------------


@pytest.mark.parametrize("value", [1, 2, 16, 64])
def test_batch_size_bounds(value: int) -> None:
    assert promptguard_cuda_batch_size_from_config({}) == DEFAULT_CUDA_BATCH_SIZE == 16
    config = {"promptguard_cuda_batch_size": value}
    assert promptguard_cuda_batch_size_from_config(config) == value


@pytest.mark.parametrize("value", [0, -1, 65, "16", 16.0, True, False, None, []])
def test_invalid_batch_size(value: object) -> None:
    with pytest.raises(PromptGuardCudaBatchSizeConfigurationError):
        promptguard_cuda_batch_size_from_config({"promptguard_cuda_batch_size": value})


def test_configure_batch_size_is_per_instance_and_reported() -> None:
    first, second = PromptGuardClassifier(), PromptGuardClassifier()
    assert first.device_state().effective_batch_size == DEFAULT_CUDA_BATCH_SIZE
    first.configure_batch_size(4)
    assert first.device_state().effective_batch_size == 4
    assert second.device_state().effective_batch_size == DEFAULT_CUDA_BATCH_SIZE
    first.configure_batch_size(32)
    assert first.device_state().effective_batch_size == 32


@pytest.fixture
def isolated_lifespan(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(retrieval_app, "spool_dir", lambda: tmp_path)
    monkeypatch.setattr(WeightAcquisition, "run", AsyncMock(return_value=False))


@pytest.mark.usefixtures("isolated_lifespan")
async def test_lifespan_configures_the_batch_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = {"promptguard_cuda_batch_size": 7}
    monkeypatch.setattr(retrieval_app, "_load_config", lambda: config)
    app = FastAPI()
    async with retrieval_app.lifespan(app):
        assert app.state.classifier.device_state().effective_batch_size == 7


@pytest.mark.usefixtures("isolated_lifespan")
async def test_lifespan_refuses_an_invalid_batch_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = {"promptguard_cuda_batch_size": 65}
    monkeypatch.setattr(retrieval_app, "_load_config", lambda: config)
    with pytest.raises(PromptGuardCudaBatchSizeConfigurationError):
        async with retrieval_app.lifespan(FastAPI()):
            pytest.fail("invalid batch size reached serving startup")


# -- the CPU path stays at batch 1 ------------------------------------------


@pytest.mark.parametrize("size", PAGE_SIZES)
def test_cpu_makes_one_forward_pass_per_window(size: int) -> None:
    chunks, windows, tokenizer = _page(size)
    model = _CountingModel()
    classifier = _installed(model, tokenizer, "cpu", chunks)
    classifier.configure_batch_size(BATCH)
    with patch.object(classifier, "_score_batched") as batched:
        scores, served = classifier.classify_windows("ignored")
    batched.assert_not_called()
    assert model.batch_sizes == [1] * size
    assert [call["text"] for call in tokenizer.calls] == chunks
    assert served == chunks
    assert scores == tiny.reference_scores(model.model, windows)


# -- the batched path -------------------------------------------------------


@pytest.mark.parametrize("size", PAGE_SIZES)
def test_forced_batched_path_matches_batch_one(size: int) -> None:
    chunks, windows, tokenizer = _page(size)
    model = _CountingModel()
    classifier = _installed(model, tokenizer, "cpu", chunks)
    scores = classifier._score_batched(
        cast(Any, model), cast(Any, tokenizer), "cpu", chunks, BATCH
    )
    reference = tiny.reference_scores(model.model, windows)

    assert len(scores) == size
    assert model.batch_sizes == [
        min(BATCH, size - start) for start in range(0, size, BATCH)
    ]
    assert tokenizer.calls == [
        {
            "text": chunks,
            "return_tensors": "pt",
            "truncation": True,
            "max_length": MAX_SEQ_LEN,
            "padding": True,
        }
    ]
    # Order: each score sits beside its own window's batch-1 score.
    for batched, single in zip(scores, reference, strict=True):
        assert abs(batched - single) < TOLERANCE


def test_cuda_snapshot_dispatches_to_the_batched_path() -> None:
    chunks, _, tokenizer = _page(5)
    model = MagicMock()
    classifier = _installed(model, tokenizer, "cuda", chunks)
    classifier.configure_batch_size(3)
    with patch.object(classifier, "_score_batched", return_value=[0.1] * 5) as batched:
        scores, served = classifier.classify_windows("ignored")
    batched.assert_called_once_with(model, tokenizer, "cuda", chunks, 3)
    assert scores == [0.1] * 5 and served == chunks
    model.assert_not_called()


def test_budget_refusal_precedes_any_forward_pass_on_the_batched_path() -> None:
    chunks, _, tokenizer = _page(3 * BATCH)
    model = MagicMock()
    classifier = _installed(model, tokenizer, "cuda", chunks)
    with (
        patch.object(classifier, "_score_batched") as batched,
        pytest.raises(PromptGuardBudgetExceededError),
    ):
        classifier.classify_windows("ignored", max_chunks=len(chunks) - 1)
    batched.assert_not_called()
    model.assert_not_called()
    assert tokenizer.calls == []
