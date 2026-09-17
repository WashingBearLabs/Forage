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

CONTRACT_VERSION = "1.2.0"
"""The retrieval sidecar's wire-shape version, carried on ``/health``.

Bump MAJOR when a field is removed/renamed or its semantics change; bump
MINOR when fields are only added.

* ``1.0.0`` — the frozen Epic 1 surface.
* ``1.1.0`` — ``/health`` gained ``cache_backend`` (``"valkey"`` | ``"memory"``),
  an additive field naming the storage the content cache selected at start
  (``feature-forage-cache-fallback`` US-003). Nothing was removed and no field
  changed meaning, so a consumer comparing MAJOR keeps working untouched.
* ``1.2.0`` — ``/search``'s ``SearchResult`` gained ``content_kind``
  (``"snippet"`` | ``"chunk"``, defaulted) and ``date`` (a strict
  ``YYYY-MM-DD`` calendar date or ``None``), both additive and defaulted; and
  ``search_unavailable`` joined the ``/search`` 422 vocabulary, naming an
  exhausted non-SearXNG provider chain — a new enum *member*, MINOR under
  ``contract/GOVERNANCE.md`` ruling (b) and carrying that ruling's
  announcement obligation. Two ``/search`` refusal ``reason`` *texts* also
  narrowed in ``search-provider-abstraction`` US-002 and ride this bump:
  ``searxng_error`` now reads ``SearXNG returned HTTP error (http_<status>)``
  and ``searxng_unavailable`` now reads ``SearXNG not reachable at
  <scheme://host:port>: <detail>`` — no exception text, no userinfo — neither
  changing a code, a status, or the body shape. Nothing was removed and no
  field changed meaning, so a consumer comparing MAJOR keeps working
  untouched. This version is **held**: ``tests/golden/contract_1_2_0.json`` is
  regenerated in place across ``search-provider-abstraction`` specs 2-4 until
  the ``v1.1.0`` image publishes it.

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
# /search result content kind
# ---------------------------------------------------------------------------

ContentKind = Literal[
    "snippet",
    "chunk",
]
"""What kind of content one ``SearchResult`` carries (contract ``1.2.0``).

``SearchResult.content_kind`` (``models.py``) is typed with this alias, so —
like ``DegradedReason`` — it is a **response-validation gate** rather than
documentation: a kind a provider invents that is not a member here fails
FastAPI's response validation instead of reaching a consumer.

``snippet`` is a search engine's own result summary (SearXNG's ``content``);
``chunk`` is a passage a provider extracted from the page itself. The
distinction is the consumer's, not the pipeline's: both kinds traverse the
same sanitization loop under the same length bound. The set is closed at
``1.2.0`` — ``chunk`` is declared here before it has a producer precisely so
that the first provider to emit one is not a contract change.
"""

CONTENT_KIND_SNIPPET: ContentKind = "snippet"
CONTENT_KIND_CHUNK: ContentKind = "chunk"

CONTENT_KINDS = frozenset(get_args(ContentKind))

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
    "search_unavailable",
]
"""``POST /search`` upstream failure codes (all 422).

The two ``searxng_*`` codes are the legacy pair, and they are now
*chain-shaped* rather than provider-shaped: ``orchestrator`` raises them only
when the configured chain is exactly one provider named ``searxng``, which is
the default deployment and the only one that existed before the
``SearchProvider`` seam. ``search_unavailable`` (contract ``1.2.0``) is the
general code for every other chain — its ``reason`` is the closed
``"<provider_name>: <failure_class>"`` composition, never a URL and never
exception text.
"""

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
"""Every error code the service can put on the wire — eighteen, deduplicated.

Ten ``/extract`` codes, plus the five ``/retrieve``-only refusals
(``content_too_large`` is shared), plus the three ``/search`` codes.
"""

ERROR_CODES = frozenset(get_args(ErrorCode))

# ---------------------------------------------------------------------------
# /metrics closed-key-set overflow bucket
# ---------------------------------------------------------------------------

METRICS_OTHER_BUCKET = "other"

# ---------------------------------------------------------------------------
# /search per-request policy
# ---------------------------------------------------------------------------

POLICY_EXCLUDED_ALL_PROVIDERS = "policy_excluded_all_providers"
"""The ``search_unavailable`` ``reason`` when per-request policy narrows the
effective provider chain to nothing (``search-policy-and-health`` US-010).

``search_unavailable``'s reason takes exactly two shapes: the chain-order
``"<provider>: <failure_class>"`` list ``orchestrator.py`` composes on an
exhausted chain, or this fixed literal, raised by ``retrieval_app.py`` before
``run_search_pipeline`` is ever called, when a request's own ``providers`` /
``allow_paid_fallback`` policy excludes every provider the deployment
configured. Never a mix of the two."""
