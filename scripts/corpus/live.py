"""Load a live classifier for the corpus tools, refusing unsafe environments.

``resolve_and_load`` is the one place the recorder and the parity tool turn a
model id into a resident classifier. It refuses, with one fixed reason word and
nothing else, anywhere the booted app and the tool could end up on different
models or devices: ``model_env_set``, ``device_env_set``,
``model_id_not_allowed``, ``not_pinned``, ``not_loaded``.

The device is applied **only** through the ``device_settings`` argument, never
read from ``FORAGE_DEVICE`` / ``FORAGE_DEVICE_FALLBACK``: those variables
refuse the run, so a shell setting can neither be honoured nor silently
ignored. A cuda request always runs with fallback ``refuse``, because a parity
run that quietly landed on CPU would prove nothing. Output is reason words
only; no value from the environment is ever echoed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from typing import Final

import model_fetcher
from promptguard.classifier import PromptGuardClassifier
from promptguard.device import (
    DEVICE_ENV_VAR,
    DEVICE_FALLBACK_ENV_VAR,
    DeviceSettings,
    probe_cuda,
)

_MODEL_ENV: Final[tuple[str, ...]] = (
    model_fetcher.MODEL_ID_ENV_VAR,
    model_fetcher.MODEL_REVISION_ENV_VAR,
)
_DEVICE_ENV: Final[tuple[str, ...]] = (DEVICE_ENV_VAR, DEVICE_FALLBACK_ENV_VAR)

_CPU: Final = DeviceSettings(device="cpu", fallback="cpu")


@dataclass(frozen=True)
class LoadedClassifier:
    """A classifier resident at exactly ``(model_id, revision)``."""

    classifier: PromptGuardClassifier
    model_id: str
    revision: str


@dataclass(frozen=True)
class Refusal:
    """A fixed reason word; the caller prints it and exits."""

    reason: str


def resolve_and_load(
    model_id: str,
    *,
    device_settings: DeviceSettings | None = None,
    batch_size: int | None = None,
) -> LoadedClassifier | Refusal:
    """Resolve ``model_id`` against the allowlist and pin, then load it.

    The defaults (CPU, no batch override) are the recorder's behaviour.
    """
    if any(name in os.environ for name in _MODEL_ENV):
        return Refusal("model_env_set")
    if any(name in os.environ for name in _DEVICE_ENV):
        return Refusal("device_env_set")
    if model_id not in model_fetcher.ALLOWED_MODEL_IDS:
        return Refusal("model_id_not_allowed")
    pin = model_fetcher.read_manifest_pin(model_id=model_id)
    if pin is None:
        return Refusal("not_pinned")
    revision = pin.revision

    settings = device_settings if device_settings is not None else _CPU
    boot_probe_failed = False
    if settings.device == "cuda":
        settings = replace(settings, fallback="refuse")
        boot_probe_failed = probe_cuda() != "ok"

    classifier = PromptGuardClassifier()
    classifier.configure_device(settings, boot_probe_failed)
    if batch_size is not None:
        classifier.configure_batch_size(batch_size)
    # Before `drive_all`, which patches `acquire_and_load` inside `corpus_app`.
    model_fetcher.acquire_and_load(classifier, model_id=model_id, revision=revision)
    # The revision guard: the loader refuses any revision but the manifest's,
    # so `loaded` means exactly `(model_id, revision)` is resident.
    if not classifier.loaded:
        return Refusal("not_loaded")
    return LoadedClassifier(classifier=classifier, model_id=model_id, revision=revision)
