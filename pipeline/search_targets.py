"""Boot-validated search latency targets, not sanitization revision inputs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pipeline.config_bounds import bounded_int


class SearchTargetsConfigurationError(ValueError):
    """Raised when a search latency target is wrong-typed or out of range."""


@dataclass(frozen=True, slots=True)
class SearchTargets:
    """Observational targets; neither imposes a deadline on a search."""

    promptguard_latency_target_ms: int = 1_000
    first_token_target_ms: int = 5_000


def search_targets_from_config(config: dict[str, Any]) -> SearchTargets:
    """Read both targets unconditionally so invalid values refuse boot."""
    return SearchTargets(
        promptguard_latency_target_ms=bounded_int(
            config,
            "search_promptguard_latency_target_ms",
            1_000,
            minimum=100,
            maximum=60_000,
            error=SearchTargetsConfigurationError,
        ),
        first_token_target_ms=bounded_int(
            config,
            "search_first_token_target_ms",
            5_000,
            minimum=100,
            maximum=120_000,
            error=SearchTargetsConfigurationError,
        ),
    )
