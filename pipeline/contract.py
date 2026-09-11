"""Wire contract constants for the retrieval sidecar.

Every literal that crosses the sidecar's HTTP boundary — degraded reasons,
withheld-result reasons, quarantine diagnostics, PromptGuard examination
states, the error-code vocabulary, the metrics overflow bucket, and the
contract version itself — is
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

from typing import Literal, get_args

CONTRACT_VERSION = "1.1.0"
"""The retrieval sidecar's wire-shape version, carried on ``/health``.

Bump MAJOR when a field is removed/renamed or its semantics change; bump
MINOR when fields are only added.

* ``1.0.0`` — the frozen Epic 1 surface.
* ``1.1.0`` — ``/health`` gained ``cache_backend`` (``"valkey"`` | ``"memory"``),
  an additive field naming the storage the content cache selected at start
  (``feature-forage-cache-fallback`` US-003). Nothing was removed and no field
  changed meaning, so a consumer comparing MAJOR keeps working untouched.

This is distinct from ``sanitizer_revision``
(``pipeline/sanitizer_revision.py``, already on ``/health``, cached by Poppy
at ``poppy/core/retrieval/client.py``): ``sanitizer_revision`` is a
mechanically-derived hash of pipeline *behavior* (source, model identity,
active threshold), while ``contract_version`` is a hand-bumped *wire-shape*
contract. Neither replaces the other.
"""

# ---------------------------------------------------------------------------
# /health degraded reasons
# ---------------------------------------------------------------------------

DegradedReason = Literal[
    "promptguard_unavailable",
    "cache_unavailable",
]
"""Every reason ``/health`` may list in ``degraded_reasons``.

``HealthResponse.degraded_reasons`` is typed ``list[DegradedReason]``, so this
alias is not documentation — it is a **response-validation gate**. A reason
appended at runtime that is not a member here fails FastAPI's response
validation and turns the healthcheck's own endpoint into a 500. Adding a
reason therefore means adding it *here* in the same edit that raises it; the
frozenset below is derived from this alias precisely so the two can never
disagree.
"""

DEGRADED_PROMPTGUARD_UNAVAILABLE: DegradedReason = "promptguard_unavailable"
DEGRADED_CACHE_UNAVAILABLE: DegradedReason = "cache_unavailable"

DEGRADED_REASONS = frozenset(get_args(DegradedReason))

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
# Error codes — the complete wire vocabulary, per emission site
# ---------------------------------------------------------------------------
#
# These aliases are *mirrors*: the service raises and emits exactly as it did
# before they existed, and each one names the vocabulary one emission site can
# actually produce. The parity tests in ``tests/test_contract_errors.py`` are
# the leash — they drive every site through the real routes and assert the
# mirroring model reproduces the emitted body byte-for-byte, so a shape change
# here can never quietly become documentation of something the service does
# not do.
#
# The composites below are built from the site aliases with nested ``Literal``
# (PEP 586 flattens them), so the surface vocabularies and the complete one
# cannot drift from their parts: adding a code to a site adds it everywhere it
# belongs, in one edit.

Extract422ErrorCode = Literal[
    "content_too_large",
    "content_too_large_to_classify",
    "extraction_failed",
    "invalid_filename",
    "invalid_mime_hint",
    "invalid_request_id",
    "pdf_encrypted",
    "pdf_no_text",
    "unsupported_format",
]
"""``POST /extract`` document-failure codes reaching the 422 error handler.

Seven come from ``orchestrator.DOCUMENT_FAILURE_CODES`` via
``document_failure()``; ``invalid_filename``/``invalid_mime_hint``/
``invalid_request_id`` are raised in ``retrieval_app._sanitize_upload_metadata``
before the pipeline is entered. ``busy`` is deliberately **not** here: the
handler answers it 429, not 422 (``retrieval_app.pipeline_error_handler``).
"""

EXTRACT_422_ERROR_CODES = frozenset(get_args(Extract422ErrorCode))

Admission413ErrorCode = Literal["content_too_large"]
"""The single code ``DocumentSizeLimitMiddleware`` emits, at 413.

The same code also reaches the 422 handler when the post-multipart bounded
read trips ``_spool_upload``'s cap — one code, two statuses, two body shapes.
"""

RateLimit429ErrorCode = Literal["busy"]
"""The single code ``ExtractionAdmissionMiddleware`` emits, at 429."""

ExtractErrorCode = Literal[
    Extract422ErrorCode,
    Admission413ErrorCode,
    RateLimit429ErrorCode,
]
"""The complete ``/extract`` surface vocabulary — ten codes across 413/422/429.

This is the set Poppy's client pins as ``_SIDECAR_EXTRACT_FAILURE_CODES``
(``poppy/core/retrieval/client.py``); ``tests/test_contract_errors.py`` inlines
that pinned copy verbatim and asserts equality, because the consuming file
lives in another repository.
"""

EXTRACT_ERROR_CODES = frozenset(get_args(ExtractErrorCode))

RetrieveErrorCode = Literal[
    "blocked_domain",
    "content_too_large",
    "fetch_error",
    "fetch_timeout",
    "invalid_url",
    "private_ip",
]
"""``POST /retrieve`` refusal codes (all 422).

Five are the URL-validation and fetch refusals raised in
``orchestrator.run_retrieve_pipeline``; ``content_too_large`` is the sixth and
is shared with ``/extract`` — the retrieve path raises it from
``stage5_url_audit``'s response cap, not from the ``/extract`` middleware.
"""

RETRIEVE_ERROR_CODES = frozenset(get_args(RetrieveErrorCode))

SearchErrorCode = Literal[
    "searxng_error",
    "searxng_unavailable",
]
"""``POST /search`` upstream failure codes (all 422)."""

SEARCH_ERROR_CODES = frozenset(get_args(SearchErrorCode))

Pipeline422ErrorCode = Literal[
    RetrieveErrorCode,
    SearchErrorCode,
]
"""Codes carried by the ``{error, reason, request_id}`` 422 body.

``/retrieve`` and ``/search`` share one emission site — the ``PipelineError``
handler, on a path that is not ``/extract`` — and therefore one mirroring
model. The union is the price of that single shape: a ``searxng_*`` code
cannot in fact arrive on ``/retrieve``, nor a fetch refusal on ``/search``.
Read ``RETRIEVE_ERROR_CODES`` / ``SEARCH_ERROR_CODES`` for the per-route
answer.
"""

PIPELINE_422_ERROR_CODES = frozenset(get_args(Pipeline422ErrorCode))

ErrorCode = Literal[
    ExtractErrorCode,
    Pipeline422ErrorCode,
]
"""Every error code the service can put on the wire — seventeen, deduplicated.

Ten ``/extract`` codes, plus the five ``/retrieve``-only refusals
(``content_too_large`` is shared), plus the two ``/search`` codes.
"""

ERROR_CODES = frozenset(get_args(ErrorCode))

# ---------------------------------------------------------------------------
# /metrics closed-key-set overflow bucket
# ---------------------------------------------------------------------------

METRICS_OTHER_BUCKET = "other"
