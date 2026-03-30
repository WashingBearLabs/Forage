"""Tests for url_validator — RFC1918 rejection, blocklist, DNS resolution.

All tests mock ``socket.getaddrinfo`` to avoid real DNS queries.
"""

from __future__ import annotations

import socket
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Add the retrieval service root to sys.path so modules are importable
_retrieval_root = str(
    Path(__file__).resolve().parents[2] / "services" / "retrieval"
)
if _retrieval_root not in sys.path:
    sys.path.insert(0, _retrieval_root)

from url_validator import (  # noqa: E402
    BlockedDomainError,
    PrivateIPError,
    _is_private_ip,
    validate_url,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_addrinfo(ip: str) -> list[tuple]:
    """Build a minimal getaddrinfo result returning a single IP."""
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    return [(family, socket.SOCK_STREAM, 0, "", (ip, 0))]


def _fake_addrinfo_multi(*ips: str) -> list[tuple]:
    """Build a getaddrinfo result returning multiple IPs."""
    results = []
    for ip in ips:
        results.extend(_fake_addrinfo(ip))
    return results


# ---------------------------------------------------------------------------
# _is_private_ip — unit tests
# ---------------------------------------------------------------------------


class TestIsPrivateIP:
    """Direct tests on the private-IP checker."""

    @pytest.mark.parametrize(
        "ip",
        [
            "10.0.0.1",
            "10.255.255.255",
            "172.16.0.1",
            "172.31.255.255",
            "192.168.0.1",
            "192.168.255.255",
            "127.0.0.1",
            "127.255.255.255",
            "169.254.0.1",
            "169.254.255.255",
            "0.0.0.0",
        ],
    )
    def test_private_ipv4(self, ip: str) -> None:
        assert _is_private_ip(ip) is True

    @pytest.mark.parametrize(
        "ip",
        [
            "::1",
            "fe80::1",
            "fe80::abcd:1234",
            "fc00::1",
            "fd00::1",
        ],
    )
    def test_private_ipv6(self, ip: str) -> None:
        assert _is_private_ip(ip) is True

    @pytest.mark.parametrize(
        "ip",
        [
            "8.8.8.8",
            "93.184.216.34",
            "1.1.1.1",
            "203.0.113.1",
            "2607:f8b0:4004:800::200e",
        ],
    )
    def test_public_ip(self, ip: str) -> None:
        assert _is_private_ip(ip) is False

    def test_unparseable_ip_is_private(self) -> None:
        assert _is_private_ip("not-an-ip") is True

    def test_zero_ipv6(self) -> None:
        assert _is_private_ip("::") is True


# ---------------------------------------------------------------------------
# validate_url — RFC1918 rejection
# ---------------------------------------------------------------------------


class TestRFC1918Rejection:
    """URLs resolving to private IPs must be rejected before any request."""

    @pytest.mark.parametrize(
        ("ip", "label"),
        [
            ("10.0.0.1", "10/8"),
            ("10.255.255.255", "10/8 top"),
            ("172.16.0.1", "172.16/12"),
            ("172.31.255.255", "172.16/12 top"),
            ("192.168.0.1", "192.168/16"),
            ("192.168.255.255", "192.168/16 top"),
            ("127.0.0.1", "loopback"),
            ("127.0.0.2", "loopback alt"),
            ("169.254.1.1", "link-local"),
            ("0.0.0.0", "zero-address"),
        ],
    )
    def test_private_ipv4_rejected(self, ip: str, label: str) -> None:
        with patch("url_validator.socket.getaddrinfo", return_value=_fake_addrinfo(ip)):
            with pytest.raises(PrivateIPError):
                validate_url(f"https://evil.example.com/{label}")

    @pytest.mark.parametrize(
        ("ip", "label"),
        [
            ("::1", "ipv6-loopback"),
            ("fe80::1", "ipv6-link-local"),
            ("fc00::1", "ipv6-ula"),
            ("fd00::1", "ipv6-ula-fd"),
        ],
    )
    def test_private_ipv6_rejected(self, ip: str, label: str) -> None:
        with patch("url_validator.socket.getaddrinfo", return_value=_fake_addrinfo(ip)):
            with pytest.raises(PrivateIPError):
                validate_url(f"https://evil.example.com/{label}")

    def test_public_ip_passes(self) -> None:
        with patch(
            "url_validator.socket.getaddrinfo",
            return_value=_fake_addrinfo("93.184.216.34"),
        ):
            ip, hostname = validate_url("https://example.com/page")
        assert ip == "93.184.216.34"
        assert hostname == "example.com"

    def test_mixed_ips_rejected_if_any_private(self) -> None:
        """If DNS returns both public and private IPs, reject."""
        with patch(
            "url_validator.socket.getaddrinfo",
            return_value=_fake_addrinfo_multi("93.184.216.34", "10.0.0.1"),
        ):
            with pytest.raises(PrivateIPError):
                validate_url("https://example.com/")


# ---------------------------------------------------------------------------
# validate_url — localhost and .local rejection
# ---------------------------------------------------------------------------


class TestHostnameRejection:
    """Localhost and .local domains rejected before DNS resolution."""

    def test_localhost_rejected(self) -> None:
        with pytest.raises(PrivateIPError, match="localhost"):
            validate_url("https://localhost/path")

    def test_localhost_uppercase_rejected(self) -> None:
        with pytest.raises(PrivateIPError, match="localhost"):
            validate_url("https://LOCALHOST/path")

    def test_local_domain_rejected(self) -> None:
        with pytest.raises(PrivateIPError, match=r"\.local"):
            validate_url("https://myhost.local/path")

    def test_nested_local_domain_rejected(self) -> None:
        with pytest.raises(PrivateIPError, match=r"\.local"):
            validate_url("https://deep.sub.myhost.local/path")


# ---------------------------------------------------------------------------
# validate_url — domain blocklist
# ---------------------------------------------------------------------------


class TestBlockedDomains:
    """Blocked domains must be rejected without making any HTTP request."""

    def test_blocked_domain_rejected(self) -> None:
        with pytest.raises(BlockedDomainError):
            validate_url(
                "https://malware.example.com/path",
                blocked_domains=["malware.example.com"],
            )

    def test_blocked_domain_case_insensitive(self) -> None:
        with pytest.raises(BlockedDomainError):
            validate_url(
                "https://MALWARE.Example.COM/path",
                blocked_domains=["malware.example.com"],
            )

    def test_unblocked_domain_passes(self) -> None:
        with patch(
            "url_validator.socket.getaddrinfo",
            return_value=_fake_addrinfo("93.184.216.34"),
        ):
            ip, hostname = validate_url(
                "https://safe.example.com/",
                blocked_domains=["malware.example.com"],
            )
        assert ip == "93.184.216.34"

    def test_empty_blocklist_passes(self) -> None:
        with patch(
            "url_validator.socket.getaddrinfo",
            return_value=_fake_addrinfo("93.184.216.34"),
        ):
            ip, hostname = validate_url(
                "https://example.com/",
                blocked_domains=[],
            )
        assert ip == "93.184.216.34"


# ---------------------------------------------------------------------------
# validate_url — edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Malformed URLs, DNS failure, etc."""

    def test_no_hostname_raises_valueerror(self) -> None:
        with pytest.raises(ValueError, match="Cannot extract hostname"):
            validate_url("not-a-url")

    def test_dns_failure_raises_valueerror(self) -> None:
        with patch(
            "url_validator.socket.getaddrinfo",
            side_effect=socket.gaierror("Name or service not known"),
        ):
            with pytest.raises(ValueError, match="DNS resolution failed"):
                validate_url("https://nonexistent.example.invalid/")

    def test_empty_addrinfo_raises_valueerror(self) -> None:
        with patch("url_validator.socket.getaddrinfo", return_value=[]):
            with pytest.raises(ValueError, match="no results"):
                validate_url("https://example.com/")
