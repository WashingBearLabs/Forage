"""URL validation with RFC1918 rejection and DNS rebinding protection.

Validates that URLs do not resolve to private/reserved IP addresses,
enforces domain blocklists, and provides DNS pinning utilities to
prevent DNS rebinding attacks.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class PrivateIPError(Exception):
    """Raised when a URL resolves to a private or reserved IP address."""


class BlockedDomainError(Exception):
    """Raised when a URL's domain is on the blocklist."""


# ---------------------------------------------------------------------------
# Private IP ranges
# ---------------------------------------------------------------------------

_PRIVATE_NETWORKS_V4 = [
    ipaddress.IPv4Network("0.0.0.0/8"),  # "This" network
    ipaddress.IPv4Network("10.0.0.0/8"),  # Private (RFC1918)
    ipaddress.IPv4Network("100.64.0.0/10"),  # Shared address space (CGN, RFC6598)
    ipaddress.IPv4Network("127.0.0.0/8"),  # Loopback
    ipaddress.IPv4Network("169.254.0.0/16"),  # Link-local
    ipaddress.IPv4Network("172.16.0.0/12"),  # Private (RFC1918)
    ipaddress.IPv4Network("192.0.0.0/24"),  # IETF protocol assignments
    ipaddress.IPv4Network("192.0.2.0/24"),  # TEST-NET-1 (documentation)
    ipaddress.IPv4Network("192.168.0.0/16"),  # Private (RFC1918)
    ipaddress.IPv4Network("198.18.0.0/15"),  # Benchmarking (RFC2544)
    ipaddress.IPv4Network("198.51.100.0/24"),  # TEST-NET-2 (documentation)
    ipaddress.IPv4Network("203.0.113.0/24"),  # TEST-NET-3 (documentation)
    ipaddress.IPv4Network("224.0.0.0/4"),  # Multicast
    ipaddress.IPv4Network("240.0.0.0/4"),  # Reserved for future use
    ipaddress.IPv4Network("255.255.255.255/32"),  # Broadcast
]

_PRIVATE_NETWORKS_V6 = [
    ipaddress.IPv6Network("::1/128"),  # Loopback
    ipaddress.IPv6Network("fe80::/10"),  # Link-local
    ipaddress.IPv6Network("fc00::/7"),  # Unique local address
    # IPv4-mapped (also caught by the ipv4_mapped check)
    ipaddress.IPv6Network("::ffff:0:0/96"),
    ipaddress.IPv6Network("2001:db8::/32"),  # Documentation
    ipaddress.IPv6Network("ff00::/8"),  # Multicast
]

_BLOCKED_HOSTNAMES = {"localhost"}
_BLOCKED_SUFFIXES = {".local"}


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _is_private_ip(ip_str: str) -> bool:
    """Return True if *ip_str* is a private/reserved/loopback address."""
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # Unparseable → treat as unsafe

    # Explicit zero-address check
    if ip_str in ("0.0.0.0", "::"):
        return True

    # Check IPv4-mapped IPv6 addresses (e.g. ::ffff:192.168.1.1)
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        return any(addr.ipv4_mapped in net for net in _PRIVATE_NETWORKS_V4)

    if isinstance(addr, ipaddress.IPv4Address):
        return any(addr in net for net in _PRIVATE_NETWORKS_V4)

    # IPv6
    return any(addr in net for net in _PRIVATE_NETWORKS_V6)


def _check_hostname_blocklist(hostname: str) -> None:
    """Reject hostnames that are inherently private (localhost, .local)."""
    lower = hostname.lower()
    if lower in _BLOCKED_HOSTNAMES:
        raise PrivateIPError(f"Hostname '{hostname}' is blocked (localhost)")
    for suffix in _BLOCKED_SUFFIXES:
        if lower.endswith(suffix):
            raise PrivateIPError(f"Hostname '{hostname}' is blocked ({suffix} domain)")


async def validate_url(
    url: str,
    blocked_domains: list[str] | None = None,
) -> tuple[str, str]:
    """Validate a URL: check blocklist, resolve DNS, reject private IPs.

    Parameters
    ----------
    url:
        The URL to validate.
    blocked_domains:
        Optional list of blocked domain names (case-insensitive).

    Returns
    -------
    A tuple of ``(resolved_ip, hostname)`` for the first public IP found.

    Raises
    ------
    PrivateIPError
        If the URL resolves to a private/reserved IP, or is localhost / .local.
    BlockedDomainError
        If the URL's domain is in the blocklist.
    ValueError
        If the URL cannot be parsed.
    """
    parsed = urlparse(url)

    # Reject non-HTTP(S) schemes
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Unsupported URL scheme: {parsed.scheme}")

    hostname = parsed.hostname
    if not hostname:
        msg = f"Cannot extract hostname from URL: {url}"
        raise ValueError(msg)

    # 1. Domain blocklist
    if blocked_domains:
        lower_host = hostname.lower()
        lower_blocked = {d.lower() for d in blocked_domains}
        if lower_host in lower_blocked:
            raise BlockedDomainError(f"Domain '{hostname}' is on the blocklist")

    # 2. Hostname-level rejection (localhost, .local)
    _check_hostname_blocklist(hostname)

    # 3. DNS resolution (offloaded to thread to avoid blocking the event loop)
    loop = asyncio.get_running_loop()
    try:
        addrinfos = await loop.run_in_executor(
            None,
            socket.getaddrinfo,
            hostname,
            None,
        )
    except socket.gaierror as exc:
        msg = f"DNS resolution failed for '{hostname}': {exc}"
        raise ValueError(msg) from exc

    if not addrinfos:
        msg = f"DNS resolution returned no results for '{hostname}'"
        raise ValueError(msg)

    # 4. Check ALL resolved IPs — reject if ANY is private
    first_public_ip: str | None = None
    for _family, _type, _proto, _canonname, sockaddr in addrinfos:
        ip_str: str = str(sockaddr[0])
        if _is_private_ip(ip_str):
            raise PrivateIPError(f"URL '{url}' resolves to private IP {ip_str}")
        if first_public_ip is None:
            first_public_ip = ip_str

    if first_public_ip is None:
        msg = f"No usable IP addresses resolved for '{hostname}'"
        raise ValueError(msg)

    logger.debug(
        "URL validated: %s → %s (%s)",
        url,
        first_public_ip,
        hostname,
    )
    return first_public_ip, hostname
