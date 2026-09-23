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

CONTRACT_VERSION = "1.3.0"
"""The retrieval sidecar's wire-shape version, carried on ``/health``.

Bump MAJOR when a field is removed/renamed or its semantics change; bump
MINOR when fields are only added.

* ``1.0.0`` — the frozen Epic 1 surface.
* ``1.1.0`` — ``/health`` gained ``cache_backend`` (``"valkey"`` | ``"memory"``),
  an additive field naming the storage the content cache selected at start
  (``feature-forage-cache-fallback`` US-003). Nothing was removed and no field
  changed meaning, so a consumer comparing MAJOR keeps working untouched.
* ``1.2.0`` — ``/search``'s ``SearchResult`` gained ``content_kind``
  (``"snippet"`` | ``"chunk"``, defaulted), ``date`` (a strict
  ``YYYY-MM-DD`` calendar date or ``None``, defaulted) and ``domain`` (the
  lower-cased hostname of ``url``, required); ``SearchResponse`` gained
  ``provider_used`` (required — the serving provider's name),
  ``fallback_fired`` (defaulted) and ``provider_errors`` (defaulted);
  ``SearchRequest`` gained ``providers`` and ``allow_paid_fallback`` (both
  defaulted — a restrict-only per-request policy over the configured
  chain); ``HealthResponse`` gained ``search_providers`` (the resolved
  chain's names, in traversal order) and its ``capabilities`` description
  now names ``brave_api_key`` alongside ``search_sanitization``; and
  ``search_unavailable`` joined the ``/search`` 422 vocabulary, naming an
  exhausted provider chain — a new enum *member*, MINOR under
  ``contract/GOVERNANCE.md`` ruling (b) and carrying that ruling's
  announcement obligation. ``/metrics``'s ``search`` section gained three
  counters — ``fallback_fired``, ``paid_calls`` and
  ``policy_unknown_provider`` — pinned against the handler by
  ``tests/test_contract_metrics.py`` rather than by the golden fixture.
  Two ``/search`` refusal ``reason`` *texts* also narrowed in
  ``search-provider-abstraction`` US-002 and ride this bump:
  ``searxng_error`` now reads ``SearXNG returned HTTP error (http_<status>)``
  and ``searxng_unavailable`` now reads ``SearXNG not reachable at
  <scheme://host:port>: <detail>`` — no exception text, no userinfo — neither
  changing a code, a status, or the body shape. Every addition above is
  additive — a new field, a new enum member, or a new counter — so a
  consumer comparing MAJOR keeps working untouched; nothing was removed and
  no field changed meaning. The ``/search``/``/retrieve`` boundary text
  written into both routes' descriptions and the
  ``SearchRequest``/``RetrieveRequest`` model docstrings
  (``search-policy-and-health`` US-003): the description edits landed inside
  the same unreleased window and are subsumed by this MINOR.
* ``1.3.0`` — ``SearchResponse.omitted_by_reason`` gains ``blocked_url``
  (``OMIT_BLOCKED_URL``) for the search-time URL audit and domain policy.
  ``SearchResult.engine`` is NFC-normalised, stripped of C0/C1 controls,
  whitespace-collapsed and truncated to 64 characters; non-string or empty
  values become ``None``. It remains outside structural and PromptGuard
  scanning (GOVERNANCE ruling (e)). ``title`` and ``snippet`` are truncated
  after Stage 1 extraction, not before: padded and markup-dense inputs can
  serve different byte counts, and payload-shaped escaped markup is blocked
  as ``structural_blocked`` rather than served stripped. Both newline-preserving
  and whitespace-collapsed text forms are scanned. ``SearchResult.domain`` is
  the canonicalised ASCII host (UTS-46 punycode for internationalised names);
  ``url`` retains the provider's spelling. On ``/retrieve`` and ``/extract``,
  IPv6 literals embedding private IPv4 (6to4, Teredo, NAT64 and IPv4-compatible)
  and names under ``.localhost`` are refused ``private_ip`` rather than
  fetched (expedited MINOR without a compatibility window, ruling (f)).
  ``Pipeline422ErrorResponse.error`` gains ``busy`` and ``extraction_failed``
  (ruling (b)): both arrive only on ``/retrieve``, though the shared model
  also widens ``/search``'s enum. Admission refusal is 422 ``busy`` /
  ``admission_queue_full``, not ``/extract``'s 429; queue depth and reserved
  bytes are bounded by ``retrieve.admission_queue_depth`` and
  ``retrieve.max_queued_fetch_bytes``, with ``retrieve.fetch_concurrency``
  fixed at one. Fetched PDFs run in ``/extract``'s rlimited worker; failures
  formerly answered 500 now use ``extraction_failed`` with ``pdf_encrypted``,
  ``pdf_no_text``, ``pdf_extraction_error`` or ``pdf_spool_error`` reasons.
  A PDF over ``extraction.max_promptguard_chunks`` is ``content_too_large`` /
  ``promptguard_budget``. That reason also refuses pages over the opt-in
  ``retrieve.max_promptguard_chunks`` budget: ``0`` preserves the unbounded
  default for this minor release, ``retrieve_budget_unset`` warns of the next
  MINOR's default 256, and ``0`` remains an opt-out afterwards (ruling (g)).
  ``RetrievedContent.effective_promptguard_fail_closed``,
  ``RetrievedContent.effective_promptguard_threshold``,
  ``SearchResponse.effective_promptguard_fail_closed`` and
  ``SearchResponse.effective_promptguard_threshold`` are defaulted fields
  stamped on every 200, including cache hits. They report policy, not scanning;
  the operator floor bounds fail-closed on both fetch routes and the ceiling
  bounds both thresholds, without overriding trusted-tier classification skip
  or VERIFIED unavailable fail-open. ``/extract`` remains fail-closed and
  carries neither field. ``SearchRequest.promptguard_threshold`` is optional;
  both it and ``RetrieveRequest.promptguard_threshold`` accept null or omission
  for the validated configured default (shipped 0.85), before the operator
  ceiling (ruling (i)). Route/model boundary descriptions name the shared
  threshold, fail-closed and blocked-domain policy. Threshold descriptions
  apply to the max-score rule only: the opt-in server-side contiguity rule
  can block independently and ships disabled.
  The three ``RetrieveRequest`` domain-list descriptions specify directional
  matching: multi-label denylists cover subdomains, while bare allowlist
  entries match exactly and a leading dot opts into apex and subdomains.
  IP literals and single-label denylists match exactly. Leading-dot
  ``trusted_domains`` skips classification across the suffix;
  ``verified_domains`` degrades open when unavailable, even under the floor
  or a classification wait timeout; neither should name a multi-tenant apex.
  Canonical private-name rejection precedes caller denylists: a host matching
  both becomes ``private_ip`` rather than ``blocked_domain`` (ruling (h)).
  Optional ``SearchRequest.blocked_domains`` merges after the operator's
  ``seed_blocklist``; either omits matching results as ``blocked_url`` before
  content scanning without paid fallback. An over-budget denylist is refused
  whole with ``policy_domain_list_too_large``: ``content_too_large`` on
  ``/retrieve``, ``search_unavailable`` on ``/search``, non-retryable policy
  refusals. Allowlists instead drop their over-budget remainder.
  ``SearchResult.suspicious``'s corrected description includes unscanned
  results: on ``promptguard_unavailable: true``, consumers treat suspicious
  results as unscanned, not scanned-and-flagged. A single response can mix
  scanned and unscanned results because classification wait is one budget
  per request. ``SearchRequest.providers`` documents the paid-prefix rule
  and ``provider_used`` diagnosis: later-paid-only selection on an all-paid
  chain yields ``search_unavailable`` / ``policy_excluded_all_providers``.
  With one registered paid backend and duplicate collapse this changed
  outcome is not production-reachable (ruling (k), on (a2)'s basis);
  ``search.policy_unknown_provider`` counting is unchanged.
  ``HealthResponse.degraded_reasons`` gains ``cache_unauthenticated`` for
  unsigned Valkey; ``capabilities`` gains ``cache_hmac_key`` when Valkey
  signing is enabled at boot, independent of connectivity and absent in
  memory mode. ``HealthResponse.promptguard_model`` reports the configured
  model id whether loaded or not; ``promptguard_loaded`` still reports serving
  state. Both healthcheck descriptions now call the shipped Compose
  ``curl -fsS -o /dev/null`` probe status-only liveness, not body health;
  Docker-healthy does not imply loaded weights and Compose does not restart
  on an unhealthy probe.
  ``/metrics`` adds ``retrieve.classification_wait_timeouts``,
  ``search.classification_wait_timeouts``, ``retrieve.semaphore_saturation``,
  ``retrieve.busy_rejections``, ``retrieve.policy_invalid_domain_entry``,
  ``retrieve.policy_suffix_trusted_skip``, ``search.policy_invalid_domain_entry``
  and ``search.policy_suffix_trusted_skip`` (the last stays zero on standard-tier
  search). Domain counters report invalid/over-budget allowlist drops and
  wildcard trusted/verified resolutions. ``cache.corrupt_entries`` counts
  stored JSON/schema failures treated as misses rather than 500s; parse
  success is not authenticity. ``cache.integrity_rejects`` counts rejected
  signatures, envelopes, byte bounds and Valkey types before parsing.
  ``cache.storage_oversize_skips`` now counts Forage's write-side byte-bound
  refusals on both backends, not just memory (same meaning, wider producers,
  ruling (j)). ``search.provider_compressed_body`` and
  ``search.provider_timeouts`` count bounded upstream interactions; on a
  configured ``[searxng]``-only chain, ``searxng_unavailable`` reasons may
  end in ``unsupported_encoding``. Brave details stay internal, with only
  failure class wire-visible. ``search.promptguard_latency_target_exceeded``
  counts requests over the configurable target once per request;
  ``search.sanitization_latency_max_ms`` is the process-lifetime high-water
  mark of the whole result loop (structural scan, PromptGuard and semaphore
  wait), not a single wait. ``retrieve.promptguard_contiguity_detections``,
  ``search.promptguard_contiguity_detections`` and
  ``extraction.promptguard_contiguity_detections`` count contiguity blocks,
  including both-rule verdicts. These metrics are pinned by
  ``tests/test_contract_metrics.py``, not the schema golden.
  The request-validation 422 body no longer echoes the request:
  ``loc``, ``msg``, ``type`` per entry, at most ``_MAX_VALIDATION_ERRORS``
  (100) entries, and for this contract version ``input``, ``ctx`` and ``url``
  present with the fixed value ``"[redacted]"`` — an expedited MINOR under
  Example 6 step 1: the shipped description documented pydantic's extra keys;
  consumers reading ``detail[].input`` must stop — the three keys are dropped
  at the next MINOR (GOVERNANCE ruling (l)).
  Every addition above is additive except the request-validation 422 trim
  (ruling (l)); a consumer comparing MAJOR keeps working untouched.

This is distinct from ``sanitizer_revision``
(``pipeline/sanitizer_revision.py``, already on ``/health``, cached by Poppy
at ``poppy/core/retrieval/client.py``): ``sanitizer_revision`` is a
mechanically-derived hash of pipeline *behavior* (source, model identity,
configured max threshold and contiguity settings), while ``contract_version``
is a hand-bumped *wire-shape* contract. Neither replaces the other.
"""

# ---------------------------------------------------------------------------
# /health degraded reasons
# ---------------------------------------------------------------------------

DegradedReason = Literal[
    "promptguard_unavailable",
    "cache_unavailable",
    "cache_unauthenticated",
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
DEGRADED_CACHE_UNAUTHENTICATED: DegradedReason = "cache_unauthenticated"

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
# Contract 1.3.0. Policy, not malformation: a literal private, loopback,
# link-local, documentation-range or embedded-private-transition host, or a
# blocklisted name — distinct from OMIT_INVALID_URL, which is a URL that
# could not be parsed or canonicalised at all.
OMIT_BLOCKED_URL = "blocked_url"

OMISSION_REASONS = frozenset(
    {
        OMIT_INVALID_URL,
        OMIT_STRUCTURAL_BLOCKED,
        OMIT_INJECTION_DETECTED,
        OMIT_PROMPTGUARD_UNAVAILABLE,
        OMIT_BLOCKED_URL,
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
before the pipeline is entered. ``busy`` is deliberately **not** here: on
``/extract`` it answers 429, not 422 — ``retrieval_app.pipeline_error_handler``
picks 429 for ``busy`` on the ``/extract`` path only, and every other route's
``busy`` (``/retrieve``'s admission refusal) answers 422.
"""

EXTRACT_422_ERROR_CODES = frozenset(get_args(Extract422ErrorCode))

Admission413ErrorCode = Literal["content_too_large"]
"""The single code ``DocumentSizeLimitMiddleware`` emits, at 413.

The same code also reaches the 422 handler when the post-multipart bounded
read trips ``_spool_upload``'s cap — one code, two statuses, two body shapes.
"""

RateLimit429ErrorCode = Literal["busy"]
"""The single code ``/extract`` emits at 429, from ``ExtractionAdmissionMiddleware``.

The same literal is also a ``/retrieve`` 422 code (``RetrieveErrorCode``):
429 is ``/extract``'s status for it and nobody else's, because
``retrieval_app.pipeline_error_handler`` chooses the status by route and code
together, never by code alone.
"""

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
    # Intentionally the same literal as /extract's 429 `busy`: one word for
    # "refused for capacity" across the surface. It answers 422 here, because
    # the handler picks 429 for `busy` on /extract only.
    "busy",
    "content_too_large",
    # Intentionally the same literal as /extract's 422 `extraction_failed`:
    # both routes now parse PDFs in the same rlimited worker, and a failed
    # parse is one failure whichever route fetched the bytes.
    "extraction_failed",
    "fetch_error",
    "fetch_timeout",
    "invalid_url",
    "private_ip",
]
"""``POST /retrieve`` refusal codes (all 422).

Five are the URL-validation and fetch refusals raised in
``orchestrator.run_retrieve_pipeline``; ``content_too_large`` is the sixth and
is shared with ``/extract`` — the retrieve path raises it from
``stage5_url_audit``'s response cap and, with the reason
``PROMPTGUARD_BUDGET``, from the classification budget, not from the
``/extract`` middleware. Its handler raise site refuses an over-budget caller
denylist with ``POLICY_DOMAIN_LIST_TOO_LARGE`` before pipeline entry.
``busy`` (contract ``1.3.0``) is the seventh and is
also shared with ``/extract``: the ``/retrieve`` admission controller's
capacity refusal, reason ``RETRIEVE_ADMISSION_QUEUE_FULL``, answered 422 on
this route where ``/extract``'s middleware answers the same literal 429.
``extraction_failed`` (contract ``1.3.0``) is the eighth and is shared with
``/extract`` too: a fetched PDF the worker could not parse, or could not be
spooled for it. Its reasons — ``RETRIEVE_PDF_ENCRYPTED``,
``RETRIEVE_PDF_NO_TEXT``, ``RETRIEVE_PDF_EXTRACTION_ERROR``,
``RETRIEVE_PDF_SPOOL_ERROR`` — are reasons under ``extraction_failed``, not
members of this alias, even where the literal matches an ``/extract`` code.
"""

RETRIEVE_ERROR_CODES = frozenset(get_args(RetrieveErrorCode))

SearchErrorCode = Literal[
    "searxng_error",
    "searxng_unavailable",
    "search_unavailable",
]
"""``POST /search`` upstream failure and request-policy refusal codes (all 422).

The two ``searxng_*`` codes are the legacy pair, and they are now
*chain-shaped* rather than provider-shaped: ``orchestrator`` raises them only
when the configured chain is exactly one provider named ``searxng``, which is
the default deployment and the only one that existed before the
``SearchProvider`` seam. ``search_unavailable`` (contract ``1.2.0``) is the
general code for every other chain — its ``reason`` is the closed
``"<provider_name>: <failure_class>"`` composition, never a URL and never
exception text. ``search_unavailable`` also carries the two per-request policy
refusals, distinguished by ``reason``: ``policy_excluded_all_providers``
(``POLICY_EXCLUDED_ALL_PROVIDERS``) and ``policy_domain_list_too_large``
(``POLICY_DOMAIN_LIST_TOO_LARGE``). These are permanent client errors, not
retryable without changing the request or the configured policy/budget.
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
cannot in fact arrive on ``/retrieve``, nor a fetch refusal, ``busy`` or
``extraction_failed`` on ``/search``.
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
(``content_too_large``, ``busy`` and ``extraction_failed`` are shared), plus
the three ``/search`` codes.
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

``search_unavailable``'s reason takes exactly three shapes: the chain-order
``"<provider>: <failure_class>"`` list ``orchestrator.py`` composes on an
exhausted chain, or this fixed literal, raised by ``retrieval_app.py`` before
``run_search_pipeline`` is ever called, when a request's own ``providers`` /
``allow_paid_fallback`` policy excludes every provider the deployment
configured; or ``POLICY_DOMAIN_LIST_TOO_LARGE``, raised by the same handler
before normalising an over-budget caller denylist. Never a mix of these shapes."""

POLICY_DOMAIN_LIST_TOO_LARGE = "policy_domain_list_too_large"
"""An over-budget caller denylist, refused whole rather than partially enforced.

The route-specific 422 codes are ``content_too_large`` on ``/retrieve`` and
``search_unavailable`` on ``/search``. The request's
own raw list bytes exceed ``policy_domain_entries_max_bytes``; retrying without
changing the list or the configured budget cannot succeed.
"""

# ---------------------------------------------------------------------------
# /retrieve classification budget
# ---------------------------------------------------------------------------

PROMPTGUARD_BUDGET = "promptguard_budget"
"""The ``/retrieve`` ``content_too_large`` ``reason`` for a page over budget.

``content_too_large`` on ``/retrieve`` carries exactly three reason shapes: the
fetch-cap prose ``stage5_url_audit`` raises when a body exceeds the 10 MB
transfer limit, or this fixed literal, raised by ``orchestrator.py`` when the
extracted text of a fetched page exceeds the character ceiling derived from
``retrieve.max_promptguard_chunks``; or ``POLICY_DOMAIN_LIST_TOO_LARGE`` for an
over-budget caller denylist. Token-shaped rather than sentence-shaped
(``POLICY_EXCLUDED_ALL_PROVIDERS`` is the precedent) because it is a closed
value a consumer may branch on, not prose for a human."""

RETRIEVE_ADMISSION_QUEUE_FULL = "admission_queue_full"
"""The ``/retrieve`` 422 ``busy`` ``reason``: the admission queue is full.

Raised by ``orchestrator.run_retrieve_pipeline`` when the ``/retrieve``
admission controller refuses a request because its queue is at
``retrieve.admission_queue_depth`` or its reserved bytes would exceed
``retrieve.max_queued_fetch_bytes``. The only reason ``busy`` carries on
``/retrieve``; a closed token for the same reason as ``PROMPTGUARD_BUDGET``."""

# ---------------------------------------------------------------------------
# /retrieve fetched-PDF failure reasons (the `extraction_failed` reasons)
# ---------------------------------------------------------------------------
#
# Raised by `orchestrator.run_retrieve_pipeline` around the fetched-PDF worker
# call, most-specific first. They are `reason` values under the 422
# `extraction_failed`, not members of `RetrieveErrorCode`. A fetched PDF whose
# text is over `extraction.max_promptguard_chunks` is not among them: that is
# `content_too_large` / `PROMPTGUARD_BUDGET`, the same refusal as an
# over-budget page.

# Intentionally the same literal as /extract's `pdf_encrypted` code: the same
# failure, named the same way, as a reason here rather than a code.
RETRIEVE_PDF_ENCRYPTED = "pdf_encrypted"
# Intentionally the same literal as /extract's `pdf_no_text` code, for the
# same reason: no text layer, OCR not supported.
RETRIEVE_PDF_NO_TEXT = "pdf_no_text"
# The worker's `failed` status: a corrupt parse, the page limit, or a child
# killed by `RLIMIT_CPU`, `RLIMIT_AS` or the wall clock — the IPC vocabulary
# deliberately does not tell them apart.
RETRIEVE_PDF_EXTRACTION_ERROR = "pdf_extraction_error"
# The one host fault: the spool directory or file could not be created or
# written (ENOSPC, EACCES, a read-only or vanished temp dir, a refused spool
# directory). Logged once as the closed WARNING token `retrieve_spool_error`.
RETRIEVE_PDF_SPOOL_ERROR = "pdf_spool_error"

RETRIEVE_PDF_FAILURE_REASONS = frozenset(
    {
        RETRIEVE_PDF_ENCRYPTED,
        RETRIEVE_PDF_NO_TEXT,
        RETRIEVE_PDF_EXTRACTION_ERROR,
        RETRIEVE_PDF_SPOOL_ERROR,
    }
)
