"""Install-time device selection for the PromptGuard classifier.

``FORAGE_DEVICE`` (``cpu|cuda``) picks where the classifier runs and
``FORAGE_DEVICE_FALLBACK`` (``cpu|refuse``) picks what happens when the GPU is
not usable. Both are read from the environment once, at startup.

Nothing here echoes a configured value: a bad setting raises a fixed message
that names the variable only, and the probe logs a closed token, never
exception text. This module is deliberately not a ``_REVISION_SOURCES`` member.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger(__name__)

DEVICE_ENV_VAR = "FORAGE_DEVICE"
DEVICE_FALLBACK_ENV_VAR = "FORAGE_DEVICE_FALLBACK"

_DEVICE_VALUES = ("cpu", "cuda")
_FALLBACK_VALUES = ("cpu", "refuse")

ProbeResult = Literal["ok", "unavailable", "oom"]


class DeviceConfigurationError(ValueError):
    """A device setting is not one of its accepted values."""


@dataclass(frozen=True)
class DeviceSettings:
    """The resolved, validated device choice and failover policy."""

    device: Literal["cpu", "cuda"]
    fallback: Literal["cpu", "refuse"]


def _parse(
    environ: Mapping[str, str], name: str, accepted: tuple[str, ...]
) -> str | None:
    """Return the normalised value, ``default`` when unset/blank, ``None`` if invalid.

    The single parse both public entry points share, so they cannot disagree.
    """
    raw = environ.get(name)
    if raw is None:
        return accepted[0]
    value = raw.strip().lower()
    if not value:
        return accepted[0]
    return value if value in accepted else None


def resolve_device_settings(environ: Mapping[str, str]) -> DeviceSettings:
    """Parse and validate both variables; raise a value-free error otherwise."""
    device = _parse(environ, DEVICE_ENV_VAR, _DEVICE_VALUES)
    if device is None:
        raise DeviceConfigurationError(f"{DEVICE_ENV_VAR} must be one of: cpu, cuda")
    fallback = _parse(environ, DEVICE_FALLBACK_ENV_VAR, _FALLBACK_VALUES)
    if fallback is None:
        raise DeviceConfigurationError(
            f"{DEVICE_FALLBACK_ENV_VAR} must be one of: cpu, refuse"
        )
    return DeviceSettings(
        device="cuda" if device == "cuda" else "cpu",
        fallback="refuse" if fallback == "refuse" else "cpu",
    )


def requested_device_token(environ: Mapping[str, str]) -> str:
    """Non-raising twin of :func:`resolve_device_settings` for the revision hash.

    ``"cpu"`` or ``"cuda"`` for a valid ``FORAGE_DEVICE``, ``"invalid"`` otherwise.
    """
    device = _parse(environ, DEVICE_ENV_VAR, _DEVICE_VALUES)
    return "invalid" if device is None else device


def probe_cuda() -> ProbeResult:
    """Try to allocate on ``cuda:0``; map the outcome to a closed token."""
    result: ProbeResult = "unavailable"
    try:
        import torch

        try:
            if torch.cuda.is_available():
                torch.empty(1, device="cuda:0")
                result = "ok"
        except torch.cuda.OutOfMemoryError:
            result = "oom"
    except Exception:
        # Includes a missing or broken torch; the exception text is never logged.
        result = "unavailable"
    # A failed probe is a degradation, so it is WARNING: loud, and printed by
    # the image's stock uvicorn logging, which leaves the root logger without
    # handlers (only Python's WARNING-level last resort reaches stderr).
    level = logging.INFO if result == "ok" else logging.WARNING
    logger.log(level, "promptguard_device_probe result=%s", result)
    return result
