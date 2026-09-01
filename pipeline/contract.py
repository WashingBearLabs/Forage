"""Wire contract constants for the retrieval sidecar.

Every literal that crosses the sidecar's HTTP boundary — degraded reasons,
withheld-result reasons, quarantine diagnostics, PromptGuard examination
states, the metrics overflow bucket, and the contract version itself — is
defined exactly once here. ``retrieval_app.py``, ``orchestrator.py``,
``stage3_promptguard.py``, ``stage4_structuring.py``, and the sidecar tests
import from this module rather than restating the strings.

This module lives under ``pipeline/`` (not the sidecar root) so that
``derive_sanitizer_revision`` (``pipeline/sanitizer_revision.py``) hashes it
as part of the mechanically-derived revision: a change to a diagnostic label
or ``PromptGuardState`` must invalidate cached extractions that were sanitized
under the old contract.
"""

from __future__ import annotations

from typing import Literal

CONTRACT_VERSION = "1.0.0"
"""The retrieval sidecar's wire-shape version, carried on ``/health``.

Bump MAJOR when a field is removed/renamed or its semantics change; bump
MINOR when fields are only added. This is distinct from ``sanitizer_revision``
(``pipeline/sanitizer_revision.py``, already on ``/health``, cached by Poppy
at ``poppy/core/retrieval/client.py``): ``sanitizer_revision`` is a
mechanically-derived hash of pipeline *behavior* (source, model identity,
active threshold), while ``contract_version`` is a hand-bumped *wire-shape*
contract. Neither replaces the other.
"""

# ---------------------------------------------------------------------------
# /health degraded reasons
# ---------------------------------------------------------------------------

DEGRADED_PROMPTGUARD_UNAVAILABLE = "promptguard_unavailable"
DEGRADED_CACHE_UNAVAILABLE = "cache_unavailable"

DEGRADED_REASONS = frozenset(
    {
        DEGRADED_PROMPTGUARD_UNAVAILABLE,
        DEGRADED_CACHE_UNAVAILABLE,
    }
)

# ---------------------------------------------------------------------------
# /search withheld-result reasons
# ---------------------------------------------------------------------------

OMIT_INVALID_URL = "invalid_url"
OMIT_STRUCTURAL_BLOCKED = "structural_blocked"
OMIT_INJECTION_DETECTED = "injection_detected"
# Intentionally the same literal as DEGRADED_PROMPTGUARD_UNAVAILABLE: both mean
# "PromptGuard did not run" — one on the /health surface, one per withheld
# search result.
OMIT_PROMPTGUARD_UNAVAILABLE = "promptguard_unavailable"

OMISSION_REASONS = frozenset(
    {
        OMIT_INVALID_URL,
        OMIT_STRUCTURAL_BLOCKED,
        OMIT_INJECTION_DETECTED,
        OMIT_PROMPTGUARD_UNAVAILABLE,
    }
)

# ---------------------------------------------------------------------------
# Quarantine diagnostics
# ---------------------------------------------------------------------------

DIAG_STRUCTURAL_BLOCKED = "structural_injection_detected"
DIAG_INJECTION_DETECTED = "promptguard_injection_detected"
DIAG_PROMPTGUARD_UNAVAILABLE = "promptguard_unavailable"

DIAGNOSTICS = frozenset(
    {
        DIAG_STRUCTURAL_BLOCKED,
        DIAG_INJECTION_DETECTED,
        DIAG_PROMPTGUARD_UNAVAILABLE,
    }
)

# ---------------------------------------------------------------------------
# PromptGuard examination state
# ---------------------------------------------------------------------------

PromptGuardState = Literal[
    "scanned",
    "skipped_trusted",
    "structural_blocked",
    "unavailable_blocked",
    "unavailable_allowed",
]

# ---------------------------------------------------------------------------
# /metrics closed-key-set overflow bucket
# ---------------------------------------------------------------------------

METRICS_OTHER_BUCKET = "other"
