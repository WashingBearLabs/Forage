"""``device@cuda`` revision input and the active device in the cache key.

``inference-surface`` US-003. The *requested* device is a revision input (cuda
only, so CPU installs keep their hash); the *active* device is a cache-key
input, so a failover changes the fingerprint and never the revision.
"""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cache import cache_policy_fingerprint
from pipeline import orchestrator
from pipeline.orchestrator import run_retrieve_pipeline
from pipeline.sanitizer_revision import derive_sanitizer_revision
from promptguard.classifier import DeviceState, PromptGuardClassifier
from promptguard.device import DeviceSettings
from tests.fakes import make_mock_classifier
from tests.test_orchestrator import (
    _SAMPLE_CONFIG,
    _SAMPLE_REVISION,
    _make_retrieve_request,
    _retrieve_kwargs,
    _retrieve_patches,
)
from tests.test_promptguard_oom import FakeNet, _Tokenizer

_CONFIG: dict[str, Any] = {"promptguard_threshold": 0.85}


def _set_device_env(monkeypatch: pytest.MonkeyPatch, env: str | None) -> None:
    if env is None:
        monkeypatch.delenv("FORAGE_DEVICE", raising=False)
    else:
        monkeypatch.setenv("FORAGE_DEVICE", env)


def _state(device: str) -> DeviceState:
    return DeviceState(
        device=device,
        requested_device="cuda",
        failed_over=device == "cpu",
        failover_reason=None,
        oom_refused=False,
        fp32_precision=None,
        effective_batch_size=1,
    )


def _fingerprint(active_device: str | None, *, loaded: bool = True) -> str:
    return cache_policy_fingerprint(
        trusted_domains=[],
        verified_domains=[],
        blocked_domains=[],
        promptguard_threshold=0.85,
        promptguard_fail_closed=True,
        classifier_loaded=loaded,
        active_device=active_device,
        sanitizer_revision="r" * 64,
    )


# -- the revision ------------------------------------------------------------


def test_unset_and_cpu_share_one_revision_and_cuda_differs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revisions: dict[str | None, str] = {}
    for env in (None, "cpu", "CPU", "cuda", "tpu"):
        _set_device_env(monkeypatch, env)
        revisions[env] = derive_sanitizer_revision(_CONFIG)
    assert revisions[None] == revisions["cpu"] == revisions["CPU"]
    assert revisions["cuda"] != revisions[None]
    # An invalid value hashes no device input; the lifespan refuses it anyway.
    assert revisions["tpu"] == revisions[None]


# -- the fingerprint ---------------------------------------------------------


def test_active_cpu_cuda_and_none_give_three_fingerprints() -> None:
    fingerprints = {_fingerprint("cpu"), _fingerprint("cuda"), _fingerprint(None)}
    assert len(fingerprints) == 3
    assert _fingerprint(None, loaded=False) != _fingerprint(None, loaded=True)
    assert _fingerprint("cpu", loaded=False) != _fingerprint("cpu", loaded=True)


# -- Step 8 ------------------------------------------------------------------


def _scripted_classifier(*devices: str) -> MagicMock:
    """A loaded classifier whose ``device_state`` yields *devices* in turn."""
    classifier = make_mock_classifier(score=0.1)
    states = [_state(device) for device in devices]
    classifier.device_state = MagicMock(side_effect=states)
    return classifier


async def _retrieve(classifier: Any) -> tuple[Any, MagicMock, MagicMock]:
    cache = MagicMock()
    cache.get = AsyncMock(return_value=None)
    cache.put = AsyncMock()
    validate_patch, fetch_patch = _retrieve_patches()
    with (
        validate_patch,
        fetch_patch,
        patch.object(
            orchestrator,
            "cache_policy_fingerprint",
            wraps=orchestrator.cache_policy_fingerprint,
        ) as fingerprint,
    ):
        content = await run_retrieve_pipeline(
            _make_retrieve_request(),
            cache=cache,
            classifier=classifier,
            config=_SAMPLE_CONFIG,
            sanitizer_revision=_SAMPLE_REVISION,
            **_retrieve_kwargs(),
        )
    return content, cache, fingerprint


async def test_the_entry_device_reaches_the_fingerprint_and_the_write_proceeds() -> (
    None
):
    classifier = _scripted_classifier("cuda", "cuda")
    content, cache, fingerprint = await _retrieve(classifier)
    assert fingerprint.call_args.kwargs["active_device"] == "cuda"
    assert content.promptguard_state == "scanned"
    cache.put.assert_awaited_once()


async def test_a_bare_mock_classifier_has_no_active_device() -> None:
    classifier = make_mock_classifier(score=0.1)
    _, cache, fingerprint = await _retrieve(classifier)
    assert fingerprint.call_args.kwargs["active_device"] is None
    cache.put.assert_awaited_once()


@pytest.mark.parametrize(("entry", "exit_"), [("cuda", "cpu"), ("cpu", "cuda")])
async def test_a_device_change_between_fingerprint_and_step_8_skips_the_write(
    entry: str, exit_: str
) -> None:
    classifier = _scripted_classifier(entry, exit_)
    content, cache, fingerprint = await _retrieve(classifier)
    assert fingerprint.call_args.kwargs["active_device"] == entry
    assert content.promptguard_state == "scanned"
    cache.put.assert_not_called()


async def test_a_snapshot_appearing_after_entry_skips_the_write() -> None:
    """``None`` at the fingerprint against a value at Step 8 differs."""
    classifier = make_mock_classifier(score=0.1)
    calls: Iterator[DeviceState | None] = iter([None, _state("cpu")])

    def scripted(_classifier: object) -> DeviceState | None:
        return next(calls)

    with patch.object(orchestrator, "device_snapshot", scripted):
        _, cache, fingerprint = await _retrieve(classifier)
    assert fingerprint.call_args.kwargs["active_device"] is None
    cache.put.assert_not_called()


async def test_windows_straddling_a_swap_serve_a_scanned_body_and_never_cache() -> None:
    class _SwappingNet(FakeNet):
        """Swaps the classifier to a CPU net after its first window."""

        classifier: PromptGuardClassifier | None = None

        def __call__(self, **kwargs: Any) -> Any:
            result = super().__call__(**kwargs)
            if self.classifier is not None:
                swapped, self.classifier = self.classifier, None
                cast(Any, swapped)._active = (
                    FakeNet(SimpleNamespace(max_batch=0)),
                    "cpu",
                )
            return result

    net = _SwappingNet(SimpleNamespace(max_batch=64))
    net.cuda = True
    classifier = PromptGuardClassifier()
    target = cast(Any, classifier)
    target._active = (net, "cuda")
    target._tokenizer = _Tokenizer()
    target._loaded = True
    target._chunk_text = MagicMock(return_value=["w0", "w1"])
    classifier.configure_batch_size(1)
    classifier.configure_device(DeviceSettings("cuda", "cpu"), False)
    net.classifier = classifier

    content, cache, fingerprint = await _retrieve(classifier)

    assert fingerprint.call_args.kwargs["active_device"] == "cuda"
    assert classifier.device == "cpu"
    assert content.promptguard_state == "scanned"
    assert content.injection_detected is False
    cache.put.assert_not_called()
