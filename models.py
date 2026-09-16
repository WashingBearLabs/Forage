"""Retrieval sidecar data models — Pydantic v2 schemas."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from pipeline.contract import ContentKind, PromptGuardState

MAX_CACHE_TTL_HOURS = 8_760

# `SearchResult.date` is a strict calendar date and nothing else. The regex runs
# *before* `date.fromisoformat`, which on its own also accepts the compact
# (`20260915`) and ISO-week (`2026-W38-2`) forms — neither of which is the
# `YYYY-MM-DD` shape the contract promises. Together they are a closed filter: a
# value that survives both is a real day, so nothing free-form from a provider
# can reach the wire through this field.
_CALENDAR_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class TrustTier(StrEnum):
    """Domain trust classification."""

    TRUSTED = "trusted"
    VERIFIED = "verified"
    STANDARD = "standard"
    UNTRUSTED = "untrusted"
    BLOCKED = "blocked"


class Stage2Verdict(StrEnum):
    """Stage-2 (structural heuristic) injection verdict."""

    CLEAN = "clean"
    SUSPICIOUS = "suspicious"
    BLOCKED = "blocked"


class Stage3Verdict(StrEnum):
    """Stage-3 (PromptGuard ML) injection verdict."""

    SAFE = "safe"
    INJECTION_DETECTED = "injection_detected"


# ---------------------------------------------------------------------------
# Core response model
# ---------------------------------------------------------------------------


class RetrievedContent(BaseModel):
    """Sanitised web content returned to Poppy core.

    This is the **sole** object that crosses the sidecar boundary.
    """

    # -- Identity --
    request_id: str = Field(..., min_length=1, description="UUID for this retrieval")
    source_url: str = Field(..., min_length=1, description="Original URL requested")
    final_url: str = Field(
        ..., min_length=1, description="URL after redirects resolved"
    )
    retrieved_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="When the content was fetched (UTC)",
    )
    cache_hit: bool = Field(
        default=False, description="Whether this result came from cache"
    )
    cached_at: datetime | None = Field(
        default=None, description="When the result was cached (if cache_hit)"
    )

    # -- Content --
    title: str | None = Field(default=None, description="Page title")
    body: str = Field(
        ..., description="Sanitised main content (never raw HTML/PDF bytes)"
    )
    word_count: int = Field(..., ge=0, description="Word count of body")
    content_type: str = Field(..., description="Source format: 'html' or 'pdf'")

    # -- Trust --
    trust_score: float = Field(
        ..., ge=0.0, le=1.0, description="Composite trust score (0.0-1.0)"
    )
    trust_tier: TrustTier = Field(..., description="Resolved trust tier")
    injection_detected: bool = Field(
        default=False, description="Whether prompt-injection was detected"
    )
    injection_spans: list[str] = Field(
        default_factory=list, description="Detected injection text spans"
    )
    structural_flags: list[str] = Field(
        default_factory=list, description="Structural heuristic flags raised"
    )
    stage2_verdict: Stage2Verdict = Field(
        ..., description="Stage-2 structural heuristic verdict"
    )
    stage3_verdict: Stage3Verdict = Field(
        ..., description="Stage-3 PromptGuard ML verdict"
    )
    promptguard_state: PromptGuardState = Field(
        default="scanned",
        description=(
            "How PromptGuard examined this content: scanned, skipped_trusted, "
            "structural_blocked, unavailable_blocked, or unavailable_allowed"
        ),
    )

    # -- Provenance --
    domain: str = Field(..., min_length=1, description="Domain of final_url")
    redirect_chain: list[str] = Field(
        default_factory=list, description="Ordered list of redirect URLs"
    )
    domain_changed_on_redirect: bool = Field(
        default=False,
        description="Whether the domain changed between source_url and final_url",
    )

    # -- Extraction --
    truncation_notice: str | None = Field(
        default=None,
        description="Describes what was omitted when extract_mode='summary'",
    )

    @field_validator("content_type")
    @classmethod
    def _validate_content_type(cls, v: str) -> str:
        allowed = {"html", "pdf", "text"}
        if v not in allowed:
            msg = f"content_type must be one of {allowed}, got '{v}'"
            raise ValueError(msg)
        return v


class UploadProvenance(BaseModel):
    """Display-only metadata for an untrusted uploaded document.

    Downstream consumers must treat ``filename`` and ``mime_hint`` as display
    text only. They must never use either value as a filesystem path.
    """

    source_type: Literal["upload"] = "upload"
    filename: str = Field(..., min_length=1, description="Sanitized display filename")
    mime_hint: str | None = Field(
        default=None,
        description="Caller hint only; never authoritative for type detection",
    )


class ExtractedContent(BaseModel):
    """Sanitized content returned for an uploaded document.

    This upload-only wire model deliberately does not reuse
    :class:`RetrievedContent`: uploads have no URL provenance.
    """

    request_id: str = Field(..., min_length=1, description="Extraction request ID")
    title: str | None = Field(default=None, description="Extracted document title")
    body: str = Field(..., description="Sanitized extracted document text")
    word_count: int = Field(..., ge=0, description="Word count of body")
    content_type: str = Field(..., description="Source format: 'pdf' or 'text'")
    trust_score: float = Field(
        ..., ge=0.0, le=1.0, description="Composite trust score (0.0-1.0)"
    )
    trust_tier: TrustTier = Field(..., description="Resolved trust tier")
    injection_detected: bool = Field(
        default=False, description="Whether prompt injection was detected"
    )
    injection_spans: list[str] = Field(
        default_factory=list, description="Detected injection text spans"
    )
    structural_flags: list[str] = Field(
        default_factory=list, description="Structural heuristic flags raised"
    )
    stage2_verdict: Stage2Verdict = Field(
        ..., description="Stage-2 structural heuristic verdict"
    )
    stage3_verdict: Stage3Verdict = Field(
        ..., description="Stage-3 PromptGuard ML verdict"
    )
    promptguard_state: PromptGuardState = Field(
        default="scanned",
        description=(
            "How PromptGuard examined this content: scanned, skipped_trusted, "
            "structural_blocked, unavailable_blocked, or unavailable_allowed"
        ),
    )
    provenance: UploadProvenance
    truncation_notice: str | None = Field(
        default=None,
        description="Describes what was omitted when extract_mode='summary'",
    )
    sanitizer_revision: str = Field(
        ..., min_length=1, description="Derived sanitization pipeline revision"
    )

    @field_validator("content_type")
    @classmethod
    def _validate_content_type(cls, v: str) -> str:
        allowed = {"pdf", "text"}
        if v not in allowed:
            msg = f"content_type must be one of {allowed}, got '{v}'"
            raise ValueError(msg)
        return v


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class RetrieveRequest(BaseModel):
    """Inbound request to retrieve and sanitise a URL."""

    url: str = Field(..., min_length=1, description="URL to retrieve")
    extract_mode: Literal["summary", "full"] = Field(
        default="summary", description="Extraction mode"
    )
    cache_ttl_hours: int = Field(
        default=24,
        ge=0,
        le=MAX_CACHE_TTL_HOURS,
        description="Maximum age of cached content; zero disables caching",
    )
    trusted_domains: list[str] = Field(
        default_factory=list, description="Domains to treat as trusted"
    )
    verified_domains: list[str] = Field(
        default_factory=list, description="Domains to treat as verified"
    )
    blocked_domains: list[str] = Field(
        default_factory=list, description="Domains to block outright"
    )
    promptguard_threshold: float = Field(
        default=0.85,
        ge=0.0,
        le=1.0,
        description="PromptGuard confidence threshold",
    )
    promptguard_fail_closed: bool = Field(
        default=True,
        description=(
            "When True, block content if PromptGuard is unavailable "
            "(fail-closed). When False, allow with a trust penalty (fail-open)."
        ),
    )


class SearchRequest(BaseModel):
    """Inbound request to run a web search."""

    query: str = Field(..., min_length=1, description="Search query")
    num_results: int = Field(
        default=5, ge=1, le=20, description="Number of results to return"
    )
    promptguard_fail_closed: bool = Field(
        default=True,
        description=(
            "When True, drop search results if PromptGuard is unavailable "
            "(fail-closed). When False, allow with a suspicion marker (fail-open)."
        ),
    )


class SearchResult(BaseModel):
    """A single search result."""

    title: str = Field(..., description="Result title")
    url: str = Field(..., description="Result URL")
    snippet: str = Field(..., description="Result snippet / description")
    engine: str | None = Field(default=None, description="Search engine used")
    content_kind: ContentKind = Field(
        default="snippet",
        description=(
            "What kind of content this result carries: 'snippet' for a search "
            "engine's own summary (every SearXNG result), 'chunk' for a passage "
            "a provider extracted from the page. Those two values are the whole "
            "set. Added in contract 1.2.0."
        ),
    )
    date: str | None = Field(
        default=None,
        description=(
            "The result's publication date as a strict 'YYYY-MM-DD' calendar "
            "date. Anything else — absent, a non-string, a different format, or "
            "a day that does not exist — is null. Added in contract 1.2.0."
        ),
    )
    suspicious: bool = Field(
        default=False,
        description="Whether Stage 2 or Stage 3 flagged this result as suspicious",
    )

    @field_validator("date", mode="before")
    @classmethod
    def _strict_calendar_date(cls, value: object) -> str | None:
        """Keep *value* only if it is a real ``YYYY-MM-DD`` day; else ``None``.

        Providers hand this field through from attacker-influenced upstream
        JSON, so it is a filter rather than a parse: anything that is not a
        `str` matching ``_CALENDAR_DATE_RE`` *and* accepted by
        ``date.fromisoformat`` becomes ``None`` rather than raising. A refusal
        here would let one malformed result fail a whole search response; the
        result is served without its date instead.

        Because nothing free-form survives, the field needs no injection scan
        and no length cap — the ten-character shape is its own bound.
        """
        if not isinstance(value, str) or not _CALENDAR_DATE_RE.match(value):
            return None
        try:
            date.fromisoformat(value)
        except ValueError:
            return None
        return value


class SearchResponse(BaseModel):
    """Response wrapper for search results."""

    results: list[SearchResult] = Field(
        default_factory=lambda: [], description="Search results"
    )
    request_id: str = Field(..., min_length=1, description="UUID for this search")
    query: str = Field(..., min_length=1, description="Original query")
    unresponsive_engines: list[str] = Field(
        default_factory=list,
        description="SearXNG engines that failed to respond",
    )
    omitted_results: int = Field(
        default=0,
        ge=0,
        description=(
            "Examined candidate results the pipeline withheld, across all reasons"
        ),
    )
    omitted_by_reason: dict[str, int] = Field(
        default_factory=dict,
        description=(
            "Withheld-result counts keyed by contract.OMIT_* reason. Four keys "
            "are defined in contract 1.1.0 — 'invalid_url', "
            "'structural_blocked', 'injection_detected' and "
            "'promptguard_unavailable'. Only non-zero reasons appear. "
            "Deliberately a dict rather than an enum: a consumer sums the "
            "values and buckets keys it does not know (as /metrics does, under "
            "'other'), so a future omission reason is an additive-safe MINOR "
            "change instead of a validation failure on an old client."
        ),
    )
    unscanned_results: int = Field(
        default=0,
        ge=0,
        description="Returned results PromptGuard did not scan",
    )
    promptguard_unavailable: bool = Field(
        default=False,
        description=(
            "PromptGuard was needed but did not run on at least one examined "
            "result (withheld or returned) — a stage-2 structural block never "
            "needed a scan and does not count"
        ),
    )
