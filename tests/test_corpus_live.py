"""The shared live-classifier loader: refusals, call order, device and batch.

PYTEST_DONT_REWRITE: assertion rewriting is off for this module, as in every
corpus test module (``tests/test_corpus_lint.py``).
"""

from __future__ import annotations

import pytest

import model_fetcher
from model_fetcher import WeightsManifest
from promptguard.device import DeviceSettings
from scripts.corpus import live
from scripts.corpus.drivers import _SCRUBBED_ENV
from scripts.corpus.live import LoadedClassifier, Refusal, resolve_and_load

_MODEL = "meta-llama/Llama-Prompt-Guard-2-22M"
_PIN = "a" * 40
_CUDA = DeviceSettings(device="cuda", fallback="cpu")


class _FakeClassifier:
    def __init__(self, calls: list[str], *, loads: bool = True) -> None:
        self._calls = calls
        self._loads = loads
        self.loaded = False
        self.device_args: tuple[DeviceSettings, bool] | None = None
        self.batch: int | None = None

    def configure_device(self, settings: DeviceSettings, failed: bool) -> None:
        self._calls.append("configure_device")
        self.device_args = (settings, failed)

    def configure_batch_size(self, batch_size: int) -> None:
        self._calls.append("configure_batch_size")
        self.batch = batch_size

    def load_now(self) -> None:
        self.loaded = self._loads


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    for name in (
        "FORAGE_MODEL_ID",
        "FORAGE_MODEL_REVISION",
        "FORAGE_DEVICE",
        "FORAGE_DEVICE_FALLBACK",
    ):
        monkeypatch.delenv(name, raising=False)
    log: list[str] = []
    pin = WeightsManifest(model_id=_MODEL, revision=_PIN, entries=())

    def read_pin(*, model_id: str) -> WeightsManifest | None:
        log.append("resolve")
        return pin if model_id == _MODEL else None

    monkeypatch.setattr(model_fetcher, "read_manifest_pin", read_pin)
    return log


def _install(
    monkeypatch: pytest.MonkeyPatch,
    calls: list[str],
    *,
    probe: str = "ok",
    loads: bool = True,
) -> _FakeClassifier:
    fake = _FakeClassifier(calls, loads=loads)

    def factory() -> _FakeClassifier:
        calls.append("construct")
        return fake

    def probe_cuda() -> str:
        calls.append("probe")
        return probe

    def acquire(classifier: _FakeClassifier, **kwargs: object) -> bool:
        calls.append("acquire_and_load")
        assert kwargs == {"model_id": _MODEL, "revision": _PIN}
        classifier.load_now()
        return classifier.loaded

    monkeypatch.setattr(live, "PromptGuardClassifier", factory)
    monkeypatch.setattr(live, "probe_cuda", probe_cuda)
    monkeypatch.setattr(model_fetcher, "acquire_and_load", acquire)
    return fake


@pytest.mark.parametrize("name", ["FORAGE_MODEL_ID", "FORAGE_MODEL_REVISION"])
def test_a_model_env_is_refused(
    name: str, calls: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(name, "x")
    assert resolve_and_load(_MODEL) == Refusal("model_env_set")
    assert calls == []


@pytest.mark.parametrize("name", ["FORAGE_DEVICE", "FORAGE_DEVICE_FALLBACK"])
def test_a_device_env_is_refused_never_honoured(
    name: str, calls: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(name, "cuda")
    assert resolve_and_load(_MODEL) == Refusal("device_env_set")
    assert calls == []


def test_an_unallowlisted_model_is_refused(calls: list[str]) -> None:
    assert resolve_and_load("not/allowed") == Refusal("model_id_not_allowed")
    assert calls == []


def test_an_unpinned_model_is_refused(
    calls: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:

    def no_pin(*, model_id: str) -> None:
        return None

    monkeypatch.setattr(model_fetcher, "read_manifest_pin", no_pin)
    assert resolve_and_load(_MODEL) == Refusal("not_pinned")


def test_an_unloaded_classifier_is_refused(
    calls: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _install(monkeypatch, calls, loads=False)
    assert resolve_and_load(_MODEL) == Refusal("not_loaded")


def test_the_defaults_are_cpu_with_no_batch_override(
    calls: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _install(monkeypatch, calls)
    result = resolve_and_load(_MODEL)
    assert isinstance(result, LoadedClassifier)
    assert (result.model_id, result.revision) == (_MODEL, _PIN)
    assert calls == ["resolve", "construct", "configure_device", "acquire_and_load"]
    assert fake.device_args == (DeviceSettings("cpu", "cpu"), False)
    assert fake.batch is None


def test_cuda_runs_in_order_with_fallback_forced_to_refuse(
    calls: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _install(monkeypatch, calls)
    result = resolve_and_load(_MODEL, device_settings=_CUDA, batch_size=16)
    assert isinstance(result, LoadedClassifier)
    assert calls == [
        "resolve",
        "probe",
        "construct",
        "configure_device",
        "configure_batch_size",
        "acquire_and_load",
    ]
    assert fake.device_args == (DeviceSettings("cuda", "refuse"), False)
    assert fake.batch == 16


def test_a_failed_probe_is_passed_to_the_classifier(
    calls: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _install(monkeypatch, calls, probe="unavailable", loads=False)
    assert resolve_and_load(_MODEL, device_settings=_CUDA) == Refusal("not_loaded")
    assert fake.device_args == (DeviceSettings("cuda", "refuse"), True)


def test_cpu_does_not_probe(calls: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, calls)
    resolve_and_load(_MODEL, batch_size=4)
    assert "probe" not in calls


def test_the_driver_scrubs_both_device_variables() -> None:
    assert {"FORAGE_DEVICE", "FORAGE_DEVICE_FALLBACK"} <= set(_SCRUBBED_ENV)
