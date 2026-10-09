"""Device settings, the CUDA probe and their lifespan wiring."""

from __future__ import annotations

import logging
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI

import model_fetcher
from promptguard import device
from promptguard.device import (
    DeviceConfigurationError,
    DeviceSettings,
    probe_cuda,
    requested_device_token,
    resolve_device_settings,
)
from retrieval_app import lifespan

SENTINEL = "secret-sentinel-xyz"


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({}, DeviceSettings("cpu", "cpu")),
        (
            {"FORAGE_DEVICE": "", "FORAGE_DEVICE_FALLBACK": "  "},
            DeviceSettings("cpu", "cpu"),
        ),
        ({"FORAGE_DEVICE": " CUDA "}, DeviceSettings("cuda", "cpu")),
        (
            {"FORAGE_DEVICE": "cuda", "FORAGE_DEVICE_FALLBACK": "Refuse"},
            DeviceSettings("cuda", "refuse"),
        ),
        (
            {"FORAGE_DEVICE": "cpu", "FORAGE_DEVICE_FALLBACK": "CPU"},
            DeviceSettings("cpu", "cpu"),
        ),
    ],
)
def test_resolve_accepts_valid_values(
    env: Mapping[str, str], expected: DeviceSettings
) -> None:
    assert resolve_device_settings(env) == expected


@pytest.mark.parametrize("name", ["FORAGE_DEVICE", "FORAGE_DEVICE_FALLBACK"])
def test_invalid_value_names_the_variable_not_the_value(name: str) -> None:
    with pytest.raises(DeviceConfigurationError) as info:
        resolve_device_settings({name: SENTINEL})
    assert name in str(info.value)
    assert SENTINEL not in str(info.value)


@pytest.mark.parametrize(
    "value", [None, "", " ", "cpu", "CPU", " cuda", "Cuda", "auto", SENTINEL]
)
def test_token_agrees_with_resolve(value: str | None) -> None:
    env: dict[str, str] = {} if value is None else {"FORAGE_DEVICE": value}
    token = requested_device_token(env)
    try:
        assert resolve_device_settings(env).device == token
    except DeviceConfigurationError:
        assert token == "invalid"


def _fake_torch(*, available: bool, empty: Exception | None) -> SimpleNamespace:
    class OutOfMemoryError(RuntimeError):
        pass

    def _empty(*_args: object, **_kwargs: object) -> None:
        if empty is not None:
            raise empty

    return SimpleNamespace(
        cuda=SimpleNamespace(
            is_available=lambda: available, OutOfMemoryError=OutOfMemoryError
        ),
        empty=_empty,
    )


def test_probe_maps_the_three_results(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    ok = _fake_torch(available=True, empty=None)
    oom = _fake_torch(available=True, empty=None)
    oom.empty = _fake_torch(
        available=True, empty=oom.cuda.OutOfMemoryError(SENTINEL)
    ).empty
    cases = [
        (_fake_torch(available=False, empty=None), "unavailable"),
        (oom, "oom"),
        (_fake_torch(available=True, empty=RuntimeError(SENTINEL)), "unavailable"),
        (ok, "ok"),
    ]
    for fake, expected in cases:
        with patch.dict(sys.modules, {"torch": fake}):
            assert probe_cuda() == expected
    assert SENTINEL not in caplog.text
    assert [r.getMessage() for r in caplog.records if r.name == device.__name__] == [
        f"promptguard_device_probe result={r}" for _, r in cases
    ]


def test_probe_logs_failures_at_warning_and_ok_at_info(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    for available, level in ((False, logging.WARNING), (True, logging.INFO)):
        caplog.clear()
        with patch.dict(
            sys.modules, {"torch": _fake_torch(available=available, empty=None)}
        ):
            probe_cuda()
        assert [r.levelno for r in caplog.records if r.name == device.__name__] == [
            level
        ]


# Runs in a fresh interpreter so uvicorn's stock logging (what the image's
# `CMD ["uvicorn", ...]` gets) is the only configuration in play.
_UVICORN_PROBE_SCRIPT = """
import logging.config, sys
from types import SimpleNamespace
from uvicorn.config import LOGGING_CONFIG
logging.config.dictConfig(LOGGING_CONFIG)
class OutOfMemoryError(Exception):
    pass
def fake(available):
    return SimpleNamespace(
        cuda=SimpleNamespace(
            is_available=lambda: available, OutOfMemoryError=OutOfMemoryError
        ),
        empty=lambda *a, **k: None,
    )
from promptguard.device import probe_cuda
sys.modules["torch"] = fake(False)
assert probe_cuda() == "unavailable"
sys.modules["torch"] = fake(True)
assert probe_cuda() == "ok"
"""


def test_failed_probe_token_is_printed_under_uvicorn_default_logging() -> None:
    """The CI smoke greps `docker logs` for this token; it must reach stderr."""
    repo_root = Path(__file__).resolve().parent.parent
    proc = subprocess.run(
        [sys.executable, "-c", _UVICORN_PROBE_SCRIPT],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    output = proc.stdout + proc.stderr
    assert "promptguard_device_probe result=unavailable" in output
    # INFO stays quiet under the stock configuration; only degradation is loud.
    assert "promptguard_device_probe result=ok" not in output


async def test_refuse_with_failing_probe_raises_before_serving(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FORAGE_DEVICE", "cuda")
    monkeypatch.setenv("FORAGE_DEVICE_FALLBACK", "refuse")
    with (
        patch("promptguard.device.probe_cuda", return_value="unavailable"),
        patch("model_fetcher.WeightAcquisition") as acquisition,
        pytest.raises(DeviceConfigurationError),
    ):
        async with lifespan(FastAPI()):
            pytest.fail("served without a usable GPU")
    acquisition.assert_not_called()


async def test_cpu_fallback_with_failing_probe_starts_and_records_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FORAGE_DEVICE", "cuda")
    monkeypatch.setattr(model_fetcher, "RETRY_INITIAL_BACKOFF_S", 3600.0)
    probe_app = FastAPI()
    with (
        patch("promptguard.device.probe_cuda", return_value="oom"),
        patch(
            "model_fetcher.acquire_and_load", side_effect=RuntimeError("never loads")
        ),
    ):
        async with lifespan(probe_app):
            assert probe_app.state.boot_probe_failed is True
            assert probe_app.state.promptguard_requested_device == "cuda"


async def test_default_settings_never_probe_or_import_torch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FORAGE_DEVICE_FALLBACK", "refuse")
    monkeypatch.setattr(model_fetcher, "RETRY_INITIAL_BACKOFF_S", 3600.0)
    monkeypatch.delitem(sys.modules, "torch", raising=False)
    cpu_app = FastAPI()
    with (
        patch("promptguard.device.probe_cuda") as probe,
        patch(
            "model_fetcher.acquire_and_load", side_effect=RuntimeError("never loads")
        ),
    ):
        async with lifespan(cpu_app):
            assert cpu_app.state.boot_probe_failed is False
            assert cpu_app.state.promptguard_requested_device == "cpu"
            assert "torch" not in sys.modules
    probe.assert_not_called()
