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
import ssl
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse, urlunparse

import httpx

from url_validator import validate_url

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_REDIRECTS = 5
DEFAULT_MAX_CONTENT_BYTES = 10 * 1024 * 1024  # 10 MB


class ContentTooLargeError(Exception):
    """Raised when the response body exceeds the maximum size."""


DEFAULT_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:128.0) "
    "Gecko/20100101 Firefox/128.0",
]


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FetchResult:
    """Result of the Stage 5 URL fetch and audit."""

    final_url: str
    redirect_chain: list[str] = field(default_factory=list[str])
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

    # Use stdlib SSL context — httpx's default context builder doesn't
    # always find system CA certs on all OpenSSL/Debian combinations.
    ssl_context = ssl.create_default_context()

    response_body = b""
    final_status_code = 0
    final_content_type = ""

    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=False,
        verify=ssl_context,
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
            pinned_url = urlunparse(
                (
                    parsed.scheme,
                    f"{ip_host}:{parsed.port}" if parsed.port else ip_host,
                    parsed.path,
                    parsed.params,
                    parsed.query,
                    parsed.fragment,
                )
            )

            headers: dict[str, str] = {"Host": hostname}
            if selected_ua:
                headers["User-Agent"] = selected_ua

            # TLS verification remains enabled. The SNI hostname extension
            # tells the server which certificate to present, resolving the
            # hostname/IP mismatch caused by DNS pinning. The Host header
            # ensures correct virtual-host routing.
            #
            # Streaming mode: headers are read immediately; body is only
            # read on demand via aiter_bytes(). This prevents a malicious
            # server from OOM-ing the sidecar by sending a multi-GB body
            # on any hop (redirect or final) before any size check runs.
            async with client.stream(
                "GET",
                pinned_url,
                headers=headers,
                extensions={"sni_hostname": hostname},
            ) as response:
                if response.is_redirect:
                    redirect_chain.append(current_url)
                    location = response.headers.get("location", "")
                    if location:
                        # Resolve relative redirects
                        current_url = urljoin(current_url, location)
                        if hop == max_redirects - 1:
                            raise TooManyRedirectsError(
                                f"Exceeded {max_redirects} redirects for URL: {url}"
                            )
                        # Exit streaming context without reading body.
                        # Closes the connection, discarding any body the
                        # server is sending — no bytes are buffered.
                        continue
                    # No Location header — fall through and treat as final.

                # Final response (non-redirect, or redirect with no Location).
                # Fast-reject on Content-Length before reading any body bytes.
                cl = response.headers.get("content-length")
                if cl and cl.isdigit() and int(cl) > max_content_bytes:
                    raise ContentTooLargeError(
                        f"Content-Length {cl} exceeds "
                        f"{max_content_bytes} bytes for URL: {url}"
                    )

                # Stream body with incremental byte cap. Raises and closes
                # the response the moment the running total exceeds the cap.
                chunks: list[bytes] = []
                running = 0
                async for chunk in response.aiter_bytes():
                    running += len(chunk)
                    if running > max_content_bytes:
                        raise ContentTooLargeError(
                            f"Response body exceeds "
                            f"{max_content_bytes} bytes for URL: {url}"
                        )
                    chunks.append(chunk)

                response_body = b"".join(chunks)
                final_status_code = response.status_code
                final_content_type = response.headers.get("content-type", "")
                break

    source_domain = urlparse(url).netloc
    final_domain = urlparse(current_url).netloc
    domain_changed = bool(redirect_chain) and source_domain != final_domain

    return FetchResult(
        final_url=current_url,
        redirect_chain=redirect_chain,
        domain_changed_on_redirect=domain_changed,
        response_body=response_body,
        content_type=final_content_type,
        status_code=final_status_code,
    )
