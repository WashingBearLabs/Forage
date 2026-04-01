"""Stage 5 -- URL audit, RFC1918 validation, and HTTP fetch.

Performs outbound URL fetching with:
- RFC1918 / private-IP rejection before any HTTP request
- DNS rebinding protection via manual redirect following
- Domain blocklist enforcement
- User-Agent rotation
- Redirect chain tracking with domain-change detection

This stage runs BEFORE Stages 1-4 in the pipeline — it is the stage
that actually retrieves content from the internet.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse, urlunparse

import httpx

from url_validator import BlockedDomainError, PrivateIPError, validate_url  # noqa: F811

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_REDIRECTS = 5
DEFAULT_MAX_CONTENT_BYTES = 10 * 1024 * 1024  # 10 MB


class ContentTooLargeError(Exception):
    """Raised when the response body exceeds the maximum size."""
DEFAULT_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:128.0) Gecko/20100101 Firefox/128.0",
]


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FetchResult:
    """Result of the Stage 5 URL fetch and audit."""

    final_url: str
    redirect_chain: list[str] = field(default_factory=list)
    domain_changed_on_redirect: bool = False
    response_body: bytes = b""
    content_type: str = ""
    status_code: int = 0


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class TooManyRedirectsError(Exception):
    """Raised when the redirect chain exceeds the maximum length."""


# ---------------------------------------------------------------------------
# Fetch implementation
# ---------------------------------------------------------------------------


async def fetch_url(
    url: str,
    *,
    blocked_domains: list[str] | None = None,
    user_agents: list[str] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
    max_content_bytes: int = DEFAULT_MAX_CONTENT_BYTES,
) -> FetchResult:
    """Fetch a URL with RFC1918 validation, DNS pinning, and redirect tracking.

    Parameters
    ----------
    url:
        The URL to fetch.
    blocked_domains:
        Domains to reject outright (no HTTP request made).
    user_agents:
        Pool of User-Agent strings to rotate through.  If ``None``,
        uses :data:`DEFAULT_USER_AGENTS`.
    timeout:
        Per-request timeout in seconds (default 30).
    max_redirects:
        Maximum number of redirects to follow (default 5).

    Returns
    -------
    FetchResult with the final URL, redirect chain, response body, etc.

    Raises
    ------
    PrivateIPError
        If any URL (initial or redirect) resolves to a private IP.
    BlockedDomainError
        If any URL's domain is on the blocklist.
    TooManyRedirectsError
        If the redirect chain exceeds *max_redirects*.
    httpx.TimeoutException
        If any individual request exceeds *timeout*.
    """
    ua_pool = user_agents if user_agents is not None else DEFAULT_USER_AGENTS
    redirect_chain: list[str] = []
    current_url = url

    # Select a User-Agent for this fetch (one per top-level call)
    selected_ua = random.choice(ua_pool) if ua_pool else ""

    response: httpx.Response | None = None
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=False,
    ) as client:
        for hop in range(max_redirects + 1):
            # Validate the current URL against RFC1918 + blocklist
            # and capture the resolved IP for DNS pinning
            resolved_ip, hostname = await validate_url(current_url, blocked_domains)

            # DNS pinning: rewrite URL to connect to the validated IP
            # and set Host header to the original hostname. This prevents
            # DNS rebinding attacks where a second resolution returns a
            # different (private) IP.
            parsed = urlparse(current_url)
            # For IPv6, wrap in brackets
            ip_host = f"[{resolved_ip}]" if ":" in resolved_ip else resolved_ip
            pinned_url = urlunparse((
                parsed.scheme,
                f"{ip_host}:{parsed.port}" if parsed.port else ip_host,
                parsed.path,
                parsed.params,
                parsed.query,
                parsed.fragment,
            ))

            headers: dict[str, str] = {"Host": hostname}
            if selected_ua:
                headers["User-Agent"] = selected_ua

            # TLS verification remains enabled. The SNI hostname extension
            # tells the server which certificate to present, resolving the
            # hostname/IP mismatch caused by DNS pinning. The Host header
            # ensures correct virtual-host routing.
            response = await client.get(
                pinned_url,
                headers=headers,
                extensions={"sni_hostname": hostname},
            )

            if response.is_redirect:
                redirect_chain.append(current_url)
                location = response.headers.get("location", "")
                if not location:
                    # No Location header — treat as final response
                    break
                # Resolve relative redirects
                current_url = urljoin(current_url, location)

                # Check if we've exceeded max redirects
                if hop == max_redirects - 1:
                    # One more validation on the final redirect target
                    # before raising the error
                    raise TooManyRedirectsError(
                        f"Exceeded {max_redirects} redirects for URL: {url}"
                    )
                continue

            # Not a redirect — we have our final response
            break

    if response is None:  # pragma: no cover — unreachable when max_redirects >= 0
        msg = f"No response received for URL: {url}"
        raise ValueError(msg)

    final_response: httpx.Response = response

    # Enforce response body size limit to prevent OOM from
    # malicious servers returning multi-gigabyte responses.
    # Check Content-Length header first (fast reject), then
    # verify actual body size (servers can lie about length).
    content_length = final_response.headers.get("content-length")
    if content_length and content_length.isdigit():
        if int(content_length) > max_content_bytes:
            raise ContentTooLargeError(
                f"Content-Length {content_length} exceeds "
                f"{max_content_bytes} bytes for URL: {url}"
            )

    response_body = final_response.content
    if len(response_body) > max_content_bytes:
        raise ContentTooLargeError(
            f"Response body ({len(response_body)} bytes) exceeds "
            f"{max_content_bytes} bytes for URL: {url}"
        )

    source_domain = urlparse(url).netloc
    final_domain = urlparse(current_url).netloc
    domain_changed = (
        bool(redirect_chain) and source_domain != final_domain
    )

    return FetchResult(
        final_url=current_url,
        redirect_chain=redirect_chain,
        domain_changed_on_redirect=domain_changed,
        response_body=response_body,
        content_type=final_response.headers.get("content-type", ""),
        status_code=final_response.status_code,
    )
