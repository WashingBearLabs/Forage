"""Retrieval sidecar data models — Pydantic v2 schemas."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from pipeline.contract import CONTENT_KIND_SNIPPET, ContentKind, PromptGuardState

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
    effective_promptguard_fail_closed: bool = Field(
        default=True,
        description=(
            "Policy applied to this request's promptguard_fail_closed flag, "
            "bounded by the operator's floor. Decides behaviour only when the "
            "classifier is unavailable (absent or classification wait timed out), "
            "not whether content was scanned; read promptguard_state for that. "
            "Neither effective policy field overrides caller-supplied trust tiers: "
            "a trusted_domains match skips classification (trusted_tier), and a "
            "verified_domains match (VERIFIED) degrades open when unavailable."
        ),
    )
    effective_promptguard_threshold: float = Field(
        default=0.85,
        ge=0.0,
        le=1.0,
        description=(
            "Block threshold applied to this request, bounded by the operator's "
            "ceiling. Reports policy, not whether content was scanned; read "
            "promptguard_state for that. Neither effective policy field overrides "
            "caller-supplied trust tiers: a trusted_domains match skips "
            "classification (trusted_tier), and a verified_domains match "
            "(VERIFIED) degrades open when the classifier is unavailable."
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
    """Inbound request to retrieve and sanitise a URL.

    `/retrieve` fetches and sanitizes one caller-named URL through the full
    pipeline, cached by `sanitizer_revision`; `/search` finds and returns
    provider-extracted content for a query across sources — snippets or
    chunks, per result `content_kind` — from the configured provider chain,
    every result sanitized, never cached. Only `/retrieve` honours
    `cache_ttl_hours`, `extract_mode`, `trusted_domains` and `verified_domains`;
    only `/search` honours `allow_paid_fallback`, `num_results` and `providers`
    and scans every result at trust tier `standard`.
    Shared by both routes: `blocked_domains`, `promptguard_threshold` and
    `promptguard_fail_closed`. On both routes, an omitted or null threshold
    uses the validated `config.yaml` default (shipped as 0.85), then
    `promptguard_threshold_ceiling` bounds the requested or default value.
    """

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
        default_factory=list,
        description=(
            "Domains to treat as trusted: bare entries match exactly; a leading dot "
            "covers the apex and every subdomain; an IP literal matches only itself. "
            "A leading-dot trusted_domains entry skips injection classification for "
            "every host under that suffix. Never name a multi-tenant or registry-level "
            "apex (.co.uk, .github.io, .s3.amazonaws.com). "
            "policy_suffix_trusted_skip counts wildcard-caused resolution to either "
            "trusted or verified (counter added by US-007)."
        ),
    )
    verified_domains: list[str] = Field(
        default_factory=list,
        description=(
            "Domains to treat as verified: bare entries match exactly; a leading dot "
            "covers the apex and every subdomain; an IP literal matches only itself. "
            "A leading-dot verified_domains entry makes every host under that suffix "
            "degrade open when the classifier is unavailable, including under "
            "promptguard_fail_closed_floor and a load-triggered classification wait "
            "timeout. Never name a multi-tenant or registry-level apex "
            "(.co.uk, .github.io, .s3.amazonaws.com). "
            "policy_suffix_trusted_skip counts "
            "wildcard-caused resolution to either trusted or verified "
            "(counter added by US-007)."
        ),
    )
    blocked_domains: list[str] = Field(
        default_factory=list,
        description=(
            "Domains to block outright: blocked_domains always covers subdomains "
            "for multi-label names, with or without a leading dot; an IP literal "
            "matches only itself. Single-label entries match exactly. Upgrade note: "
            "existing multi-label entries now cover subdomains; review apex entries "
            "before upgrading, because a multi-tenant apex removes every tenant. "
            "Single-label entries keep matching exactly as before."
        ),
    )
    promptguard_threshold: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "PromptGuard confidence threshold; null or omitted uses the server's "
            "validated config.yaml default (shipped as 0.85). The requested or "
            "default value is bounded by promptguard_threshold_ceiling."
        ),
    )
    promptguard_fail_closed: bool = Field(
        default=True,
        description=(
            "When True, block content if PromptGuard is unavailable "
            "(fail-closed). When False, allow with a trust penalty (fail-open). "
            "Bounded by the operator's floor; trusted_domains skips classification "
            "(trusted_tier), and verified_domains (VERIFIED) degrades open when "
            "unavailable regardless of this flag."
        ),
    )


class SearchRequest(BaseModel):
    """Inbound request to run a web search.

    `/search` finds and returns provider-extracted content for a query
    across sources — snippets or chunks, per result `content_kind` — from
    the configured provider chain, every result sanitized, never cached;
    `/retrieve` fetches and sanitizes one caller-named URL through the full
    pipeline, cached by `sanitizer_revision`. Only `/search` honours
    `allow_paid_fallback`, `num_results` and `providers` and scans every result
    at trust tier `standard`; only `/retrieve` honours `cache_ttl_hours`,
    `extract_mode`, `trusted_domains` and `verified_domains`.
    Shared by both routes: `blocked_domains`, `promptguard_threshold` and
    `promptguard_fail_closed`. On both routes, an omitted or null threshold
    uses the validated `config.yaml` default (shipped as 0.85), then
    `promptguard_threshold_ceiling` bounds the requested or default value.
    """

    query: str = Field(..., min_length=1, description="Search query")
    num_results: int = Field(
        default=5, ge=1, le=20, description="Number of results to return"
    )
    promptguard_threshold: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "PromptGuard confidence threshold; null or omitted uses the server's "
            "validated config.yaml default (shipped as 0.85). The requested or "
            "default value is bounded by promptguard_threshold_ceiling."
        ),
    )
    promptguard_fail_closed: bool = Field(
        default=True,
        description=(
            "When True, drop search results if PromptGuard is unavailable "
            "(fail-closed). When False, allow with a suspicion marker (fail-open). "
            "Bounded by the operator's floor."
        ),
    )
    providers: list[str] = Field(
        default_factory=list,
        description=(
            "Restrict-only filter of the configured provider chain, in "
            "configured order: can exclude paid providers only, never add, "
            "reorder, or key one — free providers always run. A non-empty list "
            "keeps only the longest prefix of the configured paid sequence "
            "whose every member is named; a named paid provider after an unnamed "
            "paid provider is dropped, never promoted. provider_used never names "
            "a dropped provider; if you named a paid provider and provider_used "
            "is not it, check its position in FORAGE_SEARCH_PROVIDERS. On an "
            "all-paid configured chain, a later-paid-only selection leaves no "
            "provider and returns 422 search_unavailable with reason "
            "policy_excluded_all_providers. Entries are "
            "matched after strip() and lower-casing against the names "
            "/health's `search_providers` publishes. Entries beyond the "
            "first eight, and entries matching no configured provider, are "
            "ignored and counted on /metrics `search.policy_unknown_provider` "
            "rather than rejected. Empty (the default) means the configured "
            "chain runs unrestricted. Honoured from contract 1.2.0."
        ),
    )
    blocked_domains: list[str] = Field(
        default_factory=list,
        description=(
            "Domains to omit from search results as blocked_url, merged after the "
            "operator's seed_blocklist, which cannot be overridden. Multi-label "
            "names cover the apex and every dot-boundary subdomain, with or without "
            "a leading dot; single-label names and IP literals match exactly. "
            "Entries are stripped and UTS-46-canonicalised once; invalid entries "
            "are ignored and counted on search.policy_invalid_domain_entry, never "
            "echoed. No entry-count cap. A raw list over "
            "policy_domain_entries_max_bytes "
            "(UTF-8 bytes including newline separators) is refused whole with 422 "
            "search_unavailable / policy_domain_list_too_large before normalisation. "
            "Honoured from contract 1.3.0."
        ),
    )
    allow_paid_fallback: bool = Field(
        default=True,
        description=(
            "When False, excludes every paid provider from this request's "
            "effective chain regardless of `providers` — free providers "
            "always run. Applied after `providers`' own "
            "normalise-then-ignore-and-count filtering (ruling 29). One-way: "
            "can only narrow the configured chain, never widen, reorder, or "
            "key it. Honoured from contract 1.2.0."
        ),
    )


class SearchResult(BaseModel):
    """A single search result."""

    title: str = Field(..., description="Result title")
    url: str = Field(..., description="Result URL")
    domain: str = Field(
        ...,
        min_length=1,
        description=(
            "The canonicalised ASCII host of `url`, with no userinfo or port "
            "— a provenance signal, not a trust decision. A name is "
            "UTS-46-encoded, so an internationalised host appears here in "
            "punycode ('xn--strae-oqa.de') while `url` keeps the provider's "
            "spelling ('http://straße.de/'); an address literal is the raw "
            "lower-cased literal as it was written. This is the host, not the "
            "registrable domain (eTLD+1); derive that yourself if you need it. "
            "For an IPv6 literal this is the unbracketed form "
            "('2606:4700::1111') while `url` carries the bracketed form "
            "('[2606:4700::1111]') — the one case where `domain` is not a "
            "substring of `url`. Added in contract 1.2.0."
        ),
    )
    snippet: str = Field(..., description="Result snippet / description")
    engine: str | None = Field(
        default=None,
        max_length=64,
        description=(
            "Search engine used, NFC-normalized with C0/C1 controls deleted "
            "and whitespace runs collapsed, truncated to 64 characters. A "
            "non-string or an empty-after-normalisation value is null. Bound "
            "and normalisation added in contract 1.3.0 — provider-controlled "
            "provenance metadata, not page content: neither structurally "
            "scanned nor part of the PromptGuard input."
        ),
    )
    content_kind: ContentKind = Field(
        default=CONTENT_KIND_SNIPPET,
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
        description=(
            "Whether Stage 2 or Stage 3 flagged this result as suspicious — "
            "and also set for every result PromptGuard did not scan at all, "
            "either because the classifier was absent or because the "
            "request's classification wait expired while it was busy. "
            "`promptguard_unavailable` says whether any result in this "
            "response was unscanned and `unscanned_results` says how many, "
            "so the consumer rule is: on `promptguard_unavailable: true`, "
            "treat every `suspicious` result as unscanned rather than as "
            "scanned-and-flagged. Since contract 1.3.0 a single response may "
            "mix the two — the wait budget is per request, so earlier results "
            "can be scanned and later ones not."
        ),
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

    effective_promptguard_threshold: float = Field(
        default=0.85,
        ge=0.0,
        le=1.0,
        description=(
            "Policy applied to this request's PromptGuard threshold after resolving "
            "the configured default and operator ceiling, not whether results were "
            "scanned; read omissions, suspicious, promptguard_unavailable and "
            "unscanned_results for that. This route uses STANDARD tier; /retrieve "
            "retains its trusted_tier skip and VERIFIED unavailable exemption."
        ),
    )
    results: list[SearchResult] = Field(
        default_factory=lambda: [], description="Search results"
    )
    request_id: str = Field(..., min_length=1, description="UUID for this search")
    query: str = Field(..., min_length=1, description="Original query")
    provider_used: str = Field(
        ...,
        min_length=1,
        description=(
            "The serving provider's name (the token FORAGE_SEARCH_PROVIDERS "
            "names, e.g. 'searxng', 'brave'). An open string rather than an "
            "enum: a third provider is an additive change, not a validation "
            "failure on an old client."
        ),
    )
    fallback_fired: bool = Field(
        default=False,
        description=(
            "True iff the provider chain advanced past the first provider "
            "before this response was served — the per-response face of the "
            "`search.fallback_fired` /metrics counter. It records chain "
            "advancement, not which provider ultimately served: for a "
            "chain that tries a provider before a free one, this is True "
            "when the free provider ends up serving."
        ),
    )
    provider_errors: list[str] = Field(
        default_factory=list,
        description=(
            "Chain-order '<provider_name>: <failure_class>' entries for every "
            "provider tried before the one that served, from a closed "
            "failure-class vocabulary — never exception text or a URL. The "
            "only home for provider-level failures: they never affect "
            "omitted_results/omitted_by_reason or unresponsive_engines."
        ),
    )
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
            "Withheld-result counts keyed by contract.OMIT_* reason. Five keys "
            "are defined — 'invalid_url', 'structural_blocked', "
            "'injection_detected' and 'promptguard_unavailable' in contract "
            "1.1.0, and 'blocked_url' added in contract 1.3.0. 'invalid_url' is "
            "a URL that is missing, over-length, or could not be parsed or "
            "canonicalised at all; 'blocked_url' is a URL that parsed cleanly "
            "but names a literal private, loopback, link-local, "
            "documentation-range or blocklisted host — policy, not "
            "malformation. Only non-zero reasons appear. Deliberately a dict "
            "rather than an enum: a consumer sums the values and buckets keys "
            "it does not know (as /metrics does, under 'other'), so a future "
            "omission reason is an additive-safe MINOR change instead of a "
            "validation failure on an old client."
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
    effective_promptguard_fail_closed: bool = Field(
        default=True,
        description=(
            "Policy applied to this request's promptguard_fail_closed flag, "
            "bounded by the operator's floor. Decides behaviour only when the "
            "classifier is unavailable (absent or classification wait timed out), "
            "not whether results were scanned; read omissions, suspicious, "
            "promptguard_unavailable and unscanned_results for that. This route "
            "uses STANDARD tier; the floor does not override caller-supplied "
            "trust tiers on /retrieve: trusted_domains skips classification "
            "(trusted_tier), and verified_domains (VERIFIED) degrades open "
            "when unavailable."
        ),
    )
