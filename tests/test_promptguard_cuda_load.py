"""CUDA load, failover and fp32 for the PromptGuard classifier (no GPU needed)."""

from __future__ import annotations

import ast
import logging
import sys
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal, cast
from unittest.mock import patch

import pytest
import torch

from promptguard.classifier import DeviceState, PromptGuardClassifier
from promptguard.device import DeviceSettings
from scripts import promptguard_tiny_model as tiny
from tests.fakes import assert_frozen

SENTINEL = "secret-sentinel-xyz"


def _backends() -> Any:
    return cast(Any, torch.backends)


class _Param:
    def __init__(self) -> None:
        self.device = SimpleNamespace(type="cpu")


class _Inputs(dict[str, Any]):
    def __init__(self) -> None:
        super().__init__(input_ids=torch.tensor([[1, 2]]))
        self.moved_to: list[str] = []

    def to(self, device: str) -> _Inputs:
        self.moved_to.append(device)
        return self


class FakeModel:
    """Records every ``.to``; ``cuda_move`` decides what a cuda move does."""

    def __init__(self, cuda_move: str = "ok", recovery_raises: bool = False) -> None:
        self.config = SimpleNamespace(id2label={0: "BENIGN", 1: "INJECTION"})
        self.params = [_Param(), _Param()]
        self.calls: list[str] = []
        self.precision_at_move: str | None = None
        self.cuda_move = cuda_move
        self.recovery_raises = recovery_raises
        self.seen: _Inputs | None = None

    def eval(self) -> FakeModel:
        return self

    def parameters(self) -> Iterator[_Param]:
        return iter(self.params)

    def to(self, device: str) -> FakeModel:
        self.calls.append(device)
        if device == "cuda":
            self.precision_at_move = str(_backends().cuda.matmul.fp32_precision)
            if self.cuda_move == "ok":
                for p in self.params:
                    p.device = SimpleNamespace(type="cuda")
                return self
            self.params[0].device = SimpleNamespace(type="cuda")
            if self.cuda_move == "oom":
                raise torch.cuda.OutOfMemoryError(SENTINEL)
            raise RuntimeError(SENTINEL)
        if self.recovery_raises:
            raise RuntimeError(SENTINEL)
        for p in self.params:
            p.device = SimpleNamespace(type="cpu")
        return self

    def __call__(self, **kwargs: torch.Tensor) -> SimpleNamespace:
        return SimpleNamespace(logits=torch.tensor([[0.0, 1.0]]))


def _load(
    model: FakeModel,
    *,
    device: Literal["cpu", "cuda"] = "cuda",
    fallback: Literal["cpu", "refuse"] = "cpu",
    probe_failed: bool = False,
    classifier: PromptGuardClassifier | None = None,
) -> tuple[PromptGuardClassifier, bool]:
    classifier = classifier or PromptGuardClassifier()
    classifier.configure_device(
        DeviceSettings(device=device, fallback=fallback),
        probe_failed,
    )
    inputs = _Inputs()
    model.seen = inputs
    with (
        patch("transformers.AutoTokenizer.from_pretrained") as tok,
        patch(
            "transformers.AutoModelForSequenceClassification.from_pretrained",
            return_value=model,
        ),
    ):
        tok.return_value.encode.return_value = [1, 2]
        tok.return_value.return_value = inputs
        return classifier, classifier.load()


@pytest.fixture(autouse=True)
def restore_precision(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_backends().cuda.matmul, "fp32_precision", "ieee")
    monkeypatch.setattr(_backends().cudnn.conv, "fp32_precision", "ieee")


def test_tiny_module_imports_only_torch_and_transformers() -> None:
    tree = ast.parse(Path(tiny.__file__).read_text())
    roots = {
        (n.module or "").split(".")[0]
        for n in ast.walk(tree)
        if isinstance(n, ast.ImportFrom)
    } | {
        a.name.split(".")[0]
        for n in ast.walk(tree)
        if isinstance(n, ast.Import)
        for a in n.names
    }
    assert roots - set(sys.stdlib_module_names) <= {"torch", "transformers"}


def _forward_to(spy: Callable[..., Any]) -> Callable[..., Any]:
    def to(_self: object, *args: Any, **kwargs: Any) -> Any:
        return spy(*args, **kwargs)

    return to


def test_default_load_makes_no_to_call_and_scores_equal_reference() -> None:
    model = tiny.build_model()
    inputs = tiny.sample_inputs()
    chunks = [f"w{i}" for i in range(len(inputs))]
    by_text = dict(zip(chunks, inputs, strict=True))

    def tokenizer(text: str, **_: object) -> dict[str, torch.Tensor]:
        ids = torch.tensor([by_text[text]])
        return {"input_ids": ids, "attention_mask": torch.ones_like(ids)}

    cast(Any, model).config.id2label = {0: "BENIGN", 1: "INJECTION"}
    classifier = PromptGuardClassifier()
    to_calls: list[object] = []
    real_to = model.to

    def spy(*args: Any, **kwargs: Any) -> Any:
        to_calls.append(args)
        return real_to(*args, **kwargs)

    with (
        patch.object(type(model), "to", _forward_to(spy)),
        patch("transformers.AutoTokenizer.from_pretrained") as tok,
        patch(
            "transformers.AutoModelForSequenceClassification.from_pretrained",
            return_value=model,
        ),
    ):
        tok.return_value = tokenizer
        assert classifier.load()
        assert to_calls == []
        with patch.object(classifier, "_chunk_text", return_value=chunks):
            scores, _ = classifier.classify_windows("ignored")
    assert scores == tiny.reference_scores(model, inputs)
    assert classifier.device_state().device == "cpu"
    assert not classifier.failed_over


def test_cuda_applies_fp32_first_records_mode_and_moves_inputs() -> None:
    model = FakeModel()
    classifier, ok = _load(model)
    assert ok and classifier.device == "cuda"
    assert model.precision_at_move == "ieee"
    assert classifier.device_state().fp32_precision == "fp32_precision"
    assert not classifier.failed_over
    classifier.classify_windows("hello")
    assert model.seen is not None and model.seen.moved_to == ["cuda"]


def test_partial_move_then_raise_ends_all_cpu_and_fails_over(
    caplog: pytest.LogCaptureFixture,
) -> None:
    model = FakeModel(cuda_move="raise")
    with (
        caplog.at_level(logging.INFO),
        patch("torch.cuda.is_available", return_value=True),
    ):
        classifier, ok = _load(model)
    assert ok
    assert all(p.device.type == "cpu" for p in model.params)
    assert classifier.failed_over and classifier.device == "cpu"
    assert classifier.device_state().failover_reason == "load_error"
    records = [r for r in caplog.records if "promptguard_device_failover" in r.message]
    assert len(records) == 1 and records[0].levelno == logging.WARNING
    assert records[0].getMessage() == "promptguard_device_failover reason=load_error"
    assert model.calls == ["cuda", "cpu"]


def test_oom_and_unavailable_reasons() -> None:
    classifier, ok = _load(FakeModel(cuda_move="oom"))
    assert ok and classifier.device_state().failover_reason == "oom"
    with patch("torch.cuda.is_available", return_value=False):
        classifier, ok = _load(FakeModel(cuda_move="raise"))
    assert ok and classifier.device_state().failover_reason == "unavailable"


def test_recovery_failure_returns_false_then_retry_loads_cpu() -> None:
    model = FakeModel(cuda_move="raise", recovery_raises=True)
    with patch("torch.cuda.is_available", return_value=True):
        classifier, ok = _load(model)
    assert (
        not ok and not classifier.loaded and classifier.device_state().device == "cpu"
    )
    model2 = FakeModel()
    classifier, ok = _load(model2, classifier=classifier)
    assert ok and model2.calls == []
    state = classifier.device_state()
    assert state.failed_over and state.failover_reason == "load_error"
    assert state.device == "cpu"


def test_refuse_returns_false_and_stays_unloaded(
    caplog: pytest.LogCaptureFixture,
) -> None:
    model = FakeModel(cuda_move="raise")
    with (
        caplog.at_level(logging.INFO),
        patch("torch.cuda.is_available", return_value=True),
    ):
        classifier, ok = _load(model, fallback="refuse")
    assert not ok and not classifier.loaded and classifier._active is None
    assert any(
        r.getMessage() == "promptguard_device_load_failed reason=load_error"
        for r in caplog.records
    )
    assert not classifier.failed_over


def test_refuse_retry_still_fails_after_latched_recovery_error() -> None:
    model = FakeModel(cuda_move="raise", recovery_raises=True)
    with patch("torch.cuda.is_available", return_value=True):
        classifier, ok = _load(model, fallback="refuse")
    assert not ok
    classifier, ok = _load(FakeModel(), fallback="refuse", classifier=classifier)
    assert not ok and not classifier.loaded


def test_boot_probe_failure_loads_cpu_failed_over_unavailable() -> None:
    model = FakeModel()
    classifier, ok = _load(model, probe_failed=True)
    assert ok and model.calls == []
    state = classifier.device_state()
    assert state.failed_over and state.failover_reason == "unavailable"
    assert state.device == "cpu" and state.requested_device == "cuda"


def test_device_state_is_a_snapshot_taken_under_the_lock() -> None:
    classifier = PromptGuardClassifier()
    done = threading.Event()
    result: list[object] = []

    def reader() -> None:
        result.append(classifier.device_state())
        done.set()

    with classifier._state_lock:
        thread = threading.Thread(target=reader)
        thread.start()
        assert not done.wait(0.2)
    thread.join(timeout=5)
    assert done.is_set()
    state = cast(DeviceState, result[0])
    assert state == classifier.device_state()
    assert_frozen(state, "device", "cuda")


@pytest.mark.parametrize("variant", ["raise", "oom", "recovery"])
def test_no_load_path_log_contains_exception_text(
    variant: str, caplog: pytest.LogCaptureFixture
) -> None:
    model = FakeModel(
        cuda_move="oom" if variant == "oom" else "raise",
        recovery_raises=variant == "recovery",
    )
    with (
        caplog.at_level(logging.DEBUG),
        patch("torch.cuda.is_available", return_value=True),
    ):
        _load(model)
        _load(FakeModel(cuda_move="raise"), fallback="refuse")
    for record in caplog.records:
        assert SENTINEL not in record.getMessage()
        assert record.exc_info is None
