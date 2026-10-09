"""GPU out-of-memory handling for the classifier (no GPU needed).

A fake model on a claimed ``cuda`` snapshot raises ``torch.cuda.OutOfMemoryError``
past a configurable batch size; the real ``classify_windows`` loop does the rest.
"""

from __future__ import annotations

import contextlib
import copy
import logging
import threading
import time
from types import SimpleNamespace
from typing import Any, Literal, cast
from unittest.mock import MagicMock, patch

import pytest
import torch

import pipeline.stage3_promptguard as stage3
from promptguard.classifier import PromptGuardClassifier, PromptGuardUnavailableError
from promptguard.device import DeviceSettings

SENTINEL = "oom-secret-sentinel"


class _Inputs(dict[str, Any]):
    def to(self, _device: str) -> _Inputs:
        return self


class _Tokenizer:
    """Window ``wN`` becomes token id N, so a score identifies its window."""

    def __call__(self, text: str | list[str], **_: Any) -> _Inputs:
        texts = [text] if isinstance(text, str) else text
        ids = torch.tensor([[int(t[1:])] for t in texts])
        return _Inputs(input_ids=ids)


def _score(i: int) -> float:
    return float(torch.sigmoid(torch.tensor(i / 10.0)))


class FakeNet:
    """cuda-flavoured when ``cuda`` is set; raises OOM above ``max_batch``."""

    def __init__(self, config: Any) -> None:
        self.config = config
        self.cuda = False
        self.forwards: list[int] = []
        self.gate: threading.Event | None = None
        self.entered = threading.Event()
        self.mismatch = False

    def state_dict(self) -> dict[str, torch.Tensor]:
        return {"w": torch.zeros(1)}

    def load_state_dict(self, state: dict[str, torch.Tensor]) -> None:
        assert all(v.device.type == "cpu" for v in state.values())

    def eval(self) -> FakeNet:
        return self

    def __call__(self, **kwargs: torch.Tensor) -> SimpleNamespace:
        ids = kwargs["input_ids"]
        n = int(ids.shape[0])
        self.forwards.append(n)
        self.entered.set()
        if self.gate is not None:
            assert self.gate.wait(5)
        if self.mismatch:
            raise RuntimeError(
                "Expected all tensors to be on the same device " + SENTINEL
            )
        if self.cuda and n > self.config.max_batch:
            raise torch.cuda.OutOfMemoryError(SENTINEL)
        logits = torch.zeros(n, 2)
        logits[:, 1] = ids[:, 0].float() / 10.0
        return SimpleNamespace(logits=logits)


def _net(max_batch: int) -> FakeNet:
    net = FakeNet(SimpleNamespace(max_batch=max_batch))
    net.cuda = True
    return net


def _classifier(
    net: FakeNet,
    count: int,
    *,
    batch: int = 16,
    fallback: Literal["cpu", "refuse"] = "cpu",
) -> PromptGuardClassifier:
    classifier = PromptGuardClassifier()
    target = cast(Any, classifier)
    target._active = (net, "cuda")
    target._tokenizer = _Tokenizer()
    target._loaded = True
    target._chunk_text = MagicMock(return_value=[f"w{i}" for i in range(count)])
    classifier.configure_batch_size(batch)
    classifier.configure_device(DeviceSettings("cuda", fallback), False)
    return classifier


def _enter_on(
    classifier: PromptGuardClassifier, net: FakeNet, chunks: list[str]
) -> list[float]:
    """Enter the cuda loop on *net* as a call that snapshotted it earlier."""
    target = cast(Any, classifier)
    return target._classify_cuda((net, "cuda"), _Tokenizer(), chunks)


def _expected(count: int) -> list[float]:
    return [_score(i) for i in range(count)]


def _assert_scores(scores: list[float], count: int) -> None:
    assert scores == pytest.approx(_expected(count), abs=1e-6)


def test_oom_at_16_retries_at_8_keeps_order_and_stays_halved() -> None:
    net = _net(8)
    classifier = _classifier(net, 20)
    scores, chunks = classifier.classify_windows("x")
    _assert_scores(scores, 20)
    assert chunks == [f"w{i}" for i in range(20)]
    assert net.forwards[0] == 16
    assert classifier.device_state().effective_batch_size == 8
    assert classifier.oom_batch_reductions == 1
    net.forwards.clear()
    classifier.classify_windows("x")
    assert max(net.forwards) == 8
    assert classifier.oom_batch_reductions == 1
    assert not classifier.failed_over


def test_partial_scores_are_kept_across_a_mid_page_oom() -> None:
    net = _net(16)
    real = FakeNet.__call__
    state = {"calls": 0}

    def flaky(self: FakeNet, **kw: torch.Tensor) -> SimpleNamespace:
        state["calls"] += 1
        if state["calls"] == 2:
            raise torch.cuda.OutOfMemoryError(SENTINEL)
        return real(self, **kw)

    classifier = _classifier(net, 40, batch=16)
    with patch.object(FakeNet, "__call__", flaky):
        scores, _ = classifier.classify_windows("x")
    _assert_scores(scores, 40)
    assert classifier.device_state().effective_batch_size == 8


def test_two_threads_from_one_oom_event_halve_once() -> None:
    net = _net(8)
    classifier = _classifier(net, 16)
    barrier = threading.Barrier(2)
    gate = threading.Event()
    real = FakeNet.__call__
    first = threading.local()

    def synced(self: FakeNet, **kw: torch.Tensor) -> SimpleNamespace:
        if not getattr(first, "done", False):
            first.done = True
            if int(kw["input_ids"].shape[0]) > self.config.max_batch:
                barrier.wait(5)  # both OOM before either halves
        return real(self, **kw)

    del gate
    results: list[list[float]] = []

    def run() -> None:
        results.append(classifier.classify_windows("x")[0])

    with patch.object(FakeNet, "__call__", synced):
        threads = [threading.Thread(target=run) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
    assert len(results) == 2
    assert classifier.device_state().effective_batch_size == 8
    assert classifier.oom_batch_reductions == 1


def test_batch_one_oom_under_cpu_builds_from_host_state_and_swaps_once() -> None:
    net = _net(0)
    classifier = _classifier(net, 3, batch=1)
    results: list[list[float]] = []
    barrier = threading.Barrier(2)
    real = FakeNet.__call__

    def synced(self: FakeNet, **kw: torch.Tensor) -> SimpleNamespace:
        if self.cuda:
            with contextlib.suppress(threading.BrokenBarrierError):
                barrier.wait(0.5)
        return real(self, **kw)

    def run() -> None:
        results.append(classifier.classify_windows("x")[0])

    with (
        patch.object(copy, "deepcopy", side_effect=AssertionError("deepcopy")),
        patch.object(FakeNet, "__call__", synced),
    ):
        threads = [threading.Thread(target=run) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
    assert len(results) == 2
    for scores in results:
        _assert_scores(scores, 3)
    assert classifier.device_failovers == 1
    assert classifier.failed_over
    assert classifier.device == "cpu"
    assert classifier.device_state().failover_reason == "oom"
    assert classifier.oom_refusals == 0


def test_device_state_is_not_blocked_while_the_copy_builds() -> None:
    net = _net(0)
    classifier = _classifier(net, 1, batch=1)
    building = threading.Event()
    release = threading.Event()

    def slow_build(_model: object) -> FakeNet:
        building.set()
        assert release.wait(5)
        return FakeNet(SimpleNamespace(max_batch=0))

    outcome: list[list[float]] = []
    with patch.object(classifier, "_build_cpu_copy", slow_build):
        worker = threading.Thread(
            target=lambda: outcome.append(classifier.classify_windows("x")[0])
        )
        worker.start()
        assert building.wait(5)
        started = time.monotonic()
        state = classifier.device_state()
        assert time.monotonic() - started < 1
        assert state.device == "cuda"
        release.set()
        worker.join(10)
    assert outcome and classifier.device == "cpu"


def test_failing_copy_build_refuses_with_copy_failed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    net = _net(0)
    classifier = _classifier(net, 2, batch=1)
    with (
        patch.object(classifier, "_build_cpu_copy", side_effect=MemoryError(SENTINEL)),
        caplog.at_level(logging.DEBUG),
        pytest.raises(PromptGuardUnavailableError),
    ):
        classifier.classify_windows("x")
    assert not classifier.failed_over
    assert classifier.device == "cuda"
    assert classifier.oom_refused
    assert classifier.oom_refusals == 1
    assert "promptguard_oom_refused reason=copy_failed" in caplog.text
    assert SENTINEL not in caplog.text
    # A later OOM may try again, and with the build working it fails over.
    scores, _ = classifier.classify_windows("x")
    _assert_scores(scores, 2)
    assert classifier.failed_over


def test_a_thread_mid_forward_during_the_swap_finishes_on_its_snapshot() -> None:
    slow = _net(64)
    slow.gate = threading.Event()
    classifier = _classifier(slow, 2, batch=2)
    done: list[list[float]] = []
    worker = threading.Thread(
        target=lambda: done.append(classifier.classify_windows("x")[0])
    )
    worker.start()
    assert slow.entered.wait(5)
    cpu = FakeNet(SimpleNamespace(max_batch=0))
    cast(Any, classifier)._active = (cpu, "cpu")  # the swap, mid-forward
    slow.gate.set()
    worker.join(10)
    assert done
    _assert_scores(done[0], 2)


def test_device_mismatch_on_a_stale_snapshot_retries_once_on_current() -> None:
    stale = _net(64)
    stale.mismatch = True
    classifier = _classifier(stale, 2, batch=2)
    cpu = FakeNet(SimpleNamespace(max_batch=0))
    cast(Any, classifier)._active = (cpu, "cpu")
    # Enter on the stale snapshot as classify_windows would have.
    scores = _enter_on(classifier, stale, ["w0", "w1"])
    _assert_scores(scores, 2)


def test_device_mismatch_without_a_swap_and_a_second_failure_propagate() -> None:
    bad = _net(64)
    bad.mismatch = True
    classifier = _classifier(bad, 2, batch=2)
    with pytest.raises(RuntimeError, match="same device"):
        classifier.classify_windows("x")
    # Second failure after a swap: the replacement also raises -> propagates.
    other = _net(64)
    other.mismatch = True
    classifier2 = _classifier(bad, 2, batch=2)
    cast(Any, classifier2)._active = (other, "cuda")
    with pytest.raises(RuntimeError, match="same device"):
        _enter_on(classifier2, bad, ["w0", "w1"])


def test_any_runtime_error_on_a_swapped_snapshot_retries_once() -> None:
    # The swap is detected by snapshot identity, so the retry does not depend
    # on torch's wording: a differently worded error still recovers.
    stale = _net(64)
    classifier = _classifier(stale, 2, batch=2)
    cpu = FakeNet(SimpleNamespace(max_batch=0))
    cast(Any, classifier)._active = (cpu, "cpu")
    original = FakeNet.__call__

    def reworded(self: FakeNet, **kwargs: torch.Tensor) -> SimpleNamespace:
        if self is stale:
            raise RuntimeError("reworded by a torch bump")
        return original(self, **kwargs)

    with patch.object(FakeNet, "__call__", reworded):
        scores = _enter_on(classifier, stale, ["w0", "w1"])
    _assert_scores(scores, 2)
    assert cpu.forwards == [1, 1]
    assert classifier.oom_batch_reductions == 0
    assert classifier.device_failovers == 0


def test_an_oom_after_the_swap_retry_propagates_without_halving() -> None:
    first = _net(0)
    classifier = _classifier(first, 2, batch=2)
    second = _net(0)
    third = _net(0)
    target = cast(Any, classifier)
    target._active = (second, "cuda")
    original = FakeNet.__call__

    def swap_again(self: FakeNet, **kwargs: torch.Tensor) -> SimpleNamespace:
        if self is second:
            target._active = (third, "cuda")
        return original(self, **kwargs)

    with (
        patch.object(FakeNet, "__call__", swap_again),
        pytest.raises(torch.cuda.OutOfMemoryError),
    ):
        _enter_on(classifier, first, ["w0", "w1"])
    assert classifier.oom_batch_reductions == 0
    assert classifier.device_failovers == 0


def test_other_exceptions_propagate_unchanged() -> None:
    net = _net(64)
    classifier = _classifier(net, 2, batch=2)
    with (
        patch.object(FakeNet, "__call__", side_effect=RuntimeError("other")),
        pytest.raises(RuntimeError, match="other"),
    ):
        classifier.classify_windows("x")
    assert classifier.oom_batch_reductions == 0
    assert classifier.device_failovers == 0
    assert classifier.oom_refusals == 0


def test_refuse_latches_until_a_cuda_success(
    caplog: pytest.LogCaptureFixture,
) -> None:
    net = _net(0)
    classifier = _classifier(net, 2, batch=1, fallback="refuse")
    with caplog.at_level(logging.DEBUG), pytest.raises(PromptGuardUnavailableError):
        classifier.classify_windows("x")
    assert classifier.oom_refused
    assert classifier.oom_refusals == 1
    assert classifier.device == "cuda"
    assert not classifier.failed_over
    assert "promptguard_oom_refused reason=oom" in caplog.text
    assert SENTINEL not in caplog.text
    net.config.max_batch = 8
    classifier.classify_windows("x")
    assert not classifier.oom_refused
    assert classifier.oom_refusals == 1


@pytest.mark.parametrize(
    ("fail_closed", "tier"), [(True, "standard"), (False, "verified")]
)
async def test_stage3_maps_a_refusal_to_the_tiers_unavailable_result(
    fail_closed: bool, tier: str
) -> None:
    classifier = MagicMock()
    classifier.loaded = True
    classifier.classify_windows.side_effect = PromptGuardUnavailableError("oom")
    expected = stage3.unavailable_result(tier, fail_closed=fail_closed)
    result = await stage3.run_promptguard(
        "text", classifier, trust_tier=tier, fail_closed=fail_closed
    )
    assert result == expected
    assert result.score == 0.0
