"""Tests for url_validator — RFC1918 rejection, blocklist, DNS resolution.

All tests mock ``loop.getaddrinfo`` to avoid real DNS queries.
"""

from __future__ import annotations

import ipaddress
import socket
from contextlib import AbstractContextManager
from unittest.mock import MagicMock, patch

import pytest

from url_validator import (
    _BLOCKED_SUFFIXES,
    _PRIVATE_NETWORKS_V6,
    BlockedDomainError,
    CanonicalHost,
    HostRejection,
    PrivateIPError,
    _is_private_ip,
    canonical_host,
    canonicalize_host,
    is_blocklisted_hostname,
    private_address_class,
    validate_url,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# ``socket.getaddrinfo`` returns 5-tuples; the fakes below build the one shape
# these tests care about — a single IPv4/IPv6 address with no scope fields.
AddrInfo = tuple[socket.AddressFamily, socket.SocketKind, int, str, tuple[str, int]]


def _fake_addrinfo(ip: str) -> list[AddrInfo]:
    """Build a minimal getaddrinfo result returning a single IP."""
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    return [(family, socket.SOCK_STREAM, 0, "", (ip, 0))]


def _fake_addrinfo_multi(*ips: str) -> list[AddrInfo]:
    """Build a getaddrinfo result returning multiple IPs."""
    results: list[AddrInfo] = []
    for ip in ips:
        results.extend(_fake_addrinfo(ip))
    return results


def _mock_getaddrinfo(
    return_value: list[AddrInfo] | None = None,
    side_effect: BaseException | None = None,
) -> AbstractContextManager[MagicMock]:
    """Patch socket.getaddrinfo (called via run_in_executor) to return fake results."""
    return patch(
        "url_validator.socket.getaddrinfo",
        return_value=return_value,
        side_effect=side_effect,
    )


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
            "100.64.0.1",  # CGN shared address (RFC6598)
            "192.0.2.1",  # TEST-NET-1
            "198.18.0.1",  # Benchmarking (RFC2544)
            "198.51.100.1",  # TEST-NET-2
            "203.0.113.1",  # TEST-NET-3
            "224.0.0.1",  # Multicast
            "240.0.0.1",  # Reserved
            "255.255.255.255",  # Broadcast
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
            "2001:db8::1",  # Documentation range
            # The five embedded-IPv4 classes, private half. Each has a public
            # mirror in `test_public_ip` below: the pair is the proof that the
            # unwrap is prefix-guarded rather than a blanket range entry.
            "::ffff:10.0.0.1",  # IPv4-mapped
            "2002:7f00:1::",  # 6to4 of 127.0.0.1
            "2001:0:0:0::80ff:fffe",  # Teredo client 127.0.0.1 (ones-complement)
            "64:ff9b::a00:1",  # NAT64 of 10.0.0.1
            "::127.0.0.1",  # IPv4-compatible of 127.0.0.1
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
            "151.101.1.140",
            # Low 32 bits `0.0.32.14`, inside `0.0.0.0/8` — an unguarded
            # low-32 mask turns this committed row red.
            "2607:f8b0:4004:800::200e",
            "2a00:1450:4001:80e::200e",  # The same property, a real Google AAAA
            "2606:4700::1111",  # Global unicast
            # The public half of each embedded-IPv4 pair above.
            "2002:808:808::",  # 6to4 of 8.8.8.8
            "2001:0:0:0::f7f7:f7f7",  # Teredo client 8.8.8.8
            "64:ff9b::808:808",  # NAT64 of 8.8.8.8
            "::8.8.8.8",  # IPv4-compatible of 8.8.8.8
        ],
    )
    def test_public_ip(self, ip: str) -> None:
        assert _is_private_ip(ip) is False

    def test_unparseable_ip_is_private(self) -> None:
        assert _is_private_ip("not-an-ip") is True

    def test_zero_ipv6(self) -> None:
        assert _is_private_ip("::") is True


# ---------------------------------------------------------------------------
# private_address_class — US-003
# ---------------------------------------------------------------------------


class TestPrivateAddressClass:
    """The classifier `_is_private_ip` delegates to, and the token it reports."""

    def test_the_ipv6_list_is_unchanged_at_six_entries(self) -> None:
        """Transition ranges are unwrapped with guards, never blanket-listed.

        A `2002::/16`, `64:ff9b::/96` or `2001::/32` entry here would be the
        easy version of this story and the wrong one: `64:ff9b::/96` maps the
        whole public IPv4 space, so an IPv6-only DNS64/NAT64 deployment would
        find every fetch refused.
        """
        assert [str(net) for net in _PRIVATE_NETWORKS_V6] == [
            "::1/128",
            "fe80::/10",
            "fc00::/7",
            "::ffff:0.0.0.0/96",
            "2001:db8::/32",
            "ff00::/8",
        ]

    @pytest.mark.parametrize(
        ("address", "expected"),
        [
            # Literals, private in their own right.
            ("192.168.1.70", "private_literal"),
            ("10.0.0.1", "private_literal"),
            ("127.0.0.1", "private_literal"),
            ("169.254.169.254", "private_literal"),
            ("0.0.0.0", "private_literal"),
            ("::", "private_literal"),
            # `::1` also lies inside `::/96`; it must be reported as the
            # loopback literal it is, never as an embedding.
            ("::1", "private_literal"),
            ("2001:db8::1", "private_literal"),
            ("fe80::1", "private_literal"),
            # The five embeddings, each in its own branch.
            ("::ffff:10.0.0.1", "embedded_private"),
            ("2002:7f00:1::", "embedded_private"),
            ("2001:0:0:0::80ff:fffe", "embedded_private"),
            ("64:ff9b::a00:1", "embedded_private"),
            ("::127.0.0.1", "embedded_private"),
            # Public, including the public half of every pair.
            ("8.8.8.8", None),
            ("2606:4700::1111", None),
            ("::ffff:8.8.8.8", None),
            ("2002:808:808::", None),
            ("2001:0:0:0::f7f7:f7f7", None),
            ("64:ff9b::808:808", None),
            ("::8.8.8.8", None),
            ("2a00:1450:4001:80e::200e", None),
        ],
    )
    def test_the_class_names_how_the_address_was_reached(
        self, address: str, expected: str | None
    ) -> None:
        assert private_address_class(ipaddress.ip_address(address)) == expected

    def test_a_public_mapped_address_is_not_re_blocked_by_the_ipv6_list(self) -> None:
        """The unwrap returns at once rather than falling through.

        `::ffff:8.8.8.8` lies inside `::ffff:0:0/96`, which *is* in the IPv6
        list. If the mapped branch fell through instead of returning, a public
        IPv4-mapped address would be refused by the list it was unwrapped to
        avoid.
        """
        mapped = ipaddress.ip_address("::ffff:8.8.8.8")
        assert any(mapped in net for net in _PRIVATE_NETWORKS_V6)
        assert private_address_class(mapped) is None

    def test_the_teredo_client_is_the_ones_complement_not_the_literal(self) -> None:
        """A Teredo literal that *looks* like 127.0.0.1 embeds 128.255.255.254."""
        looks_private = ipaddress.IPv6Address("2001:0:0:0::7f00:1")
        assert private_address_class(looks_private) is None
        really_private = ipaddress.IPv6Address("2001:0:0:0::80ff:fffe")
        assert private_address_class(really_private) == "embedded_private"

    def test_isatap_is_deliberately_not_unwrapped(self) -> None:
        """Out of scope: the prefix is deployment-specific and not enumerable.

        The suffix `::5efe:7f00:1` spells 127.0.0.1 in ISATAP form, but under
        a public prefix there is no prefix to guard the unwrap with, so the
        address is classified on its own merits.
        """
        isatap = ipaddress.IPv6Address("2a00:1450::5efe:7f00:1")
        assert private_address_class(isatap) is None


# ---------------------------------------------------------------------------
# canonicalize_host — US-003
# ---------------------------------------------------------------------------


# Every code point UTS-46 maps to U+002E: FULL STOP itself, IDEOGRAPHIC FULL
# STOP, FULLWIDTH FULL STOP and HALFWIDTH IDEOGRAPHIC FULL STOP. The last is
# spelled as an escape because ruff's RUF001 reads a literal U+FF0E in source
# as an ambiguous character — which, for this test, is exactly the point.
_UTS46_DOTS = ("\u002e", "\u3002", "\uff0e", "\uff61")


class TestCanonicalizeHost:
    """Literals first, one trailing dot, UTS-46, then the numeric rule."""

    @pytest.mark.parametrize(
        "host",
        [
            "2606:4700::1111",
            "2001:db8::1",
            "2002:808:808::",
            "64:ff9b::808:808",
            "2001:0:0:0::f7f7:f7f7",
            "::8.8.8.8",
        ],
    )
    def test_a_colon_bearing_host_is_an_ipv6_literal(self, host: str) -> None:
        """The helper classifies; the audit's step (3b) decides."""
        result = canonicalize_host(host)
        assert isinstance(result, CanonicalHost)
        assert result.kind == "ipv6"
        assert result.address == ipaddress.IPv6Address(host)

    @pytest.mark.parametrize(
        "host",
        [
            "2606:4700::1111",
            "2606:4700:0:0:0:0:0:1111",
            "::8.8.8.8",
            "2001:0:0:0::F7F7:F7F7",
        ],
    )
    def test_the_ipv6_host_is_the_raw_literal_never_the_reserialised_form(
        self, host: str
    ) -> None:
        """`str(address)` would move `domain` for every uncompressed spelling."""
        result = canonicalize_host(host)
        assert isinstance(result, CanonicalHost)
        assert result.host == host.lower()

    def test_a_colon_bearing_host_never_reaches_the_uts46_encode(self) -> None:
        """Pinned by patching: the encode raises on U+003A for every literal.

        The order is the security property, so it is asserted rather than
        inferred from the result — a classify-after-encode implementation
        would produce the same `CanonicalHost` for a well-formed literal.
        """
        with patch(
            "url_validator.idna.encode",
            side_effect=AssertionError("the encode must not see an IPv6 literal"),
        ):
            for host in ("2606:4700::1111", "2001:db8::1", "::8.8.8.8", "fe80::zz"):
                canonicalize_host(host)

    def test_an_unparseable_literal_is_rejected_rather_than_encoded(self) -> None:
        """Dead from `/search` (US-002's rule (2) raised first), live for spec 3."""
        assert canonicalize_host("fe80::zz") == HostRejection(reason="unparseable")

    @pytest.mark.parametrize(
        ("host", "expected"),
        [
            ("localhost.", "localhost"),
            ("printer.local.", "printer.local"),
            ("EXAMPLE.COM", "example.com"),
            ("example.com", "example.com"),
        ],
    )
    def test_exactly_one_trailing_dot_is_stripped(
        self, host: str, expected: str
    ) -> None:
        result = canonicalize_host(host)
        assert isinstance(result, CanonicalHost)
        assert result.host == expected

    @pytest.mark.parametrize("host", ["localhost..", "printer.local..", "a..b.com"])
    def test_a_remaining_dot_or_empty_label_is_an_idna_rejection(
        self, host: str
    ) -> None:
        """The check the round-5 review restored.

        One strip of `localhost..` leaves `localhost.`, which the UTS-46
        encode accepts unchanged (a single root dot is legal) and which
        matches neither blocklist entry — so without this guard the host
        would have been *served*.
        """
        assert canonicalize_host(host) == HostRejection(reason="idna")

    @pytest.mark.parametrize("dot", _UTS46_DOTS)
    @pytest.mark.parametrize(
        ("host", "expected_kind", "expected_host"),
        [
            ("127.0.0.1", "ipv4", "127.0.0.1"),
            ("192.168.1.70", "ipv4", "192.168.1.70"),
            ("localhost", "name", "localhost"),
            ("printer.local", "name", "printer.local"),
        ],
    )
    def test_a_trailing_dot_uts46_maps_to_is_stripped_too(
        self, dot: str, host: str, expected_kind: str, expected_host: str
    ) -> None:
        """The strip has to be relative to the dot set UTS-46 *emits*.

        U+3002, U+FF0E and U+FF61 are not `.` going in but are coming out, so
        a guard that ran only before the encode left the empty final label in
        place: it broke the all-labels-numeric test (`127.0.0.1。` classified
        as a *name*, skipping the address audit entirely) and it made
        `localhost。` match neither blocklist entry. Both were served.
        """
        result = canonicalize_host(host + dot)
        assert isinstance(result, CanonicalHost)
        assert result.kind == expected_kind
        assert result.host == expected_host

    @pytest.mark.parametrize("dot", _UTS46_DOTS)
    def test_a_doubled_dot_uts46_maps_to_is_still_an_idna_rejection(
        self, dot: str
    ) -> None:
        """One strip, never two — the empty label survives and is refused."""
        assert canonicalize_host("localhost" + dot + dot) == HostRejection(
            reason="idna"
        )

    @pytest.mark.parametrize(
        "host",
        ["2130706433", "0177.0.0.1", "0x7f000001", "0x7f.0.0.1", "127.1", "1.2.3.4.5"],
    )
    def test_a_non_canonical_numeric_host_is_rejected_never_named(
        self, host: str
    ) -> None:
        """Never a pass-through to the name path.

        Under the earlier `^[0-9.]+$` rule both hex forms reached the name
        path, where the UTS-46 encode accepts them and a resolver answers
        127.0.0.1 — the round-3 security finding.
        """
        assert canonicalize_host(host) == HostRejection(reason="numeric_host")

    @pytest.mark.parametrize("host", ["1e100.net", "123.example.com", "0x.example.com"])
    def test_a_name_with_digit_labels_is_still_a_name(self, host: str) -> None:
        result = canonicalize_host(host)
        assert isinstance(result, CanonicalHost)
        assert result.kind == "name"

    @pytest.mark.parametrize(
        ("host", "expected"),
        [("①②⑦.⓪.⓪.①", "127.0.0.1"), ("127。0。0。1", "127.0.0.1")],
    )
    def test_an_nfkc_mapped_loopback_is_numeric_only_after_the_encode(
        self, host: str, expected: str
    ) -> None:
        """Classifying before the encode is the fail-open this order closes."""
        result = canonicalize_host(host)
        assert isinstance(result, CanonicalHost)
        assert result.kind == "ipv4"
        assert result.host == expected
        assert private_address_class(ipaddress.ip_address(expected)) == (
            "private_literal"
        )

    @pytest.mark.parametrize("host", ["foo_bar.example.com", "a" * 70 + ".com"])
    def test_a_host_the_encode_refuses_is_an_idna_rejection(self, host: str) -> None:
        """The IDNA2008 yield cut, accepted deliberately."""
        assert canonicalize_host(host) == HostRejection(reason="idna")

    def test_an_idn_host_encodes_to_punycode(self) -> None:
        """UTS-46, not the stdlib IDNA2003 codec, which folds to `strasse.de`."""
        result = canonicalize_host("straße.de")
        assert isinstance(result, CanonicalHost)
        assert result.host == "xn--strae-oqa.de"
        assert "straße.de".encode("idna").decode("ascii") == "strasse.de"

    def test_the_unicode_and_punycode_spellings_agree(self) -> None:
        unicode_form = canonicalize_host("exämple.com")
        punycode_form = canonicalize_host("xn--exmple-cua.com")
        assert isinstance(unicode_form, CanonicalHost)
        assert isinstance(punycode_form, CanonicalHost)
        assert unicode_form.host == punycode_form.host == "xn--exmple-cua.com"


class TestCanonicalHostWrapper:
    """The string wrapper spec 3's `normalize_domain_entries` consumes."""

    @pytest.mark.parametrize(
        ("host", "expected"),
        [
            ("EXAMPLE.com.", "example.com"),
            ("exämple.com", "xn--exmple-cua.com"),
            ("2606:4700::1111", "2606:4700::1111"),
            ("127.0.0.1", "127.0.0.1"),
        ],
    )
    def test_it_returns_the_canonical_string(self, host: str, expected: str) -> None:
        assert canonical_host(host) == expected

    @pytest.mark.parametrize(
        "host", ["fe80::zz", "2130706433", "foo_bar.example.com", "localhost.."]
    )
    def test_it_returns_none_for_every_rejection(self, host: str) -> None:
        """An explicit `isinstance` guard, never an attribute read."""
        assert canonical_host(host) is None


class TestBlocklistedHostname:
    """The built-in private-name list, and its public non-raising alias."""

    def test_the_suffix_set_carries_both_reserved_domains(self) -> None:
        assert {".local", ".localhost"} == _BLOCKED_SUFFIXES

    @pytest.mark.parametrize(
        "host",
        ["localhost", "api.localhost", "printer.local", "deep.sub.localhost"],
    )
    def test_a_blocklisted_name_is_reported(self, host: str) -> None:
        assert is_blocklisted_hostname(host) is True

    @pytest.mark.parametrize("host", ["example.com", "localhost.example.com"])
    def test_a_public_name_is_not(self, host: str) -> None:
        assert is_blocklisted_hostname(host) is False


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
    @pytest.mark.asyncio
    async def test_private_ipv4_rejected(self, ip: str, label: str) -> None:
        with (
            _mock_getaddrinfo(return_value=_fake_addrinfo(ip)),
            pytest.raises(PrivateIPError),
        ):
            await validate_url(f"https://evil.example.com/{label}")

    @pytest.mark.parametrize(
        ("ip", "label"),
        [
            ("::1", "ipv6-loopback"),
            ("fe80::1", "ipv6-link-local"),
            ("fc00::1", "ipv6-ula"),
            ("fd00::1", "ipv6-ula-fd"),
        ],
    )
    @pytest.mark.asyncio
    async def test_private_ipv6_rejected(self, ip: str, label: str) -> None:
        with (
            _mock_getaddrinfo(return_value=_fake_addrinfo(ip)),
            pytest.raises(PrivateIPError),
        ):
            await validate_url(f"https://evil.example.com/{label}")

    @pytest.mark.asyncio
    async def test_public_ip_passes(self) -> None:
        with _mock_getaddrinfo(return_value=_fake_addrinfo("93.184.216.34")):
            ip, hostname = await validate_url("https://example.com/page")
        assert ip == "93.184.216.34"
        assert hostname == "example.com"

    @pytest.mark.asyncio
    async def test_mixed_ips_rejected_if_any_private(self) -> None:
        """If DNS returns both public and private IPs, reject."""
        with (
            _mock_getaddrinfo(
                return_value=_fake_addrinfo_multi("93.184.216.34", "10.0.0.1"),
            ),
            pytest.raises(PrivateIPError),
        ):
            await validate_url("https://example.com/")


# ---------------------------------------------------------------------------
# validate_url — localhost and .local rejection
# ---------------------------------------------------------------------------


class TestHostnameRejection:
    """Localhost and .local domains rejected before DNS resolution."""

    @pytest.mark.asyncio
    async def test_localhost_rejected(self) -> None:
        with pytest.raises(PrivateIPError, match="localhost"):
            await validate_url("https://localhost/path")

    @pytest.mark.asyncio
    async def test_localhost_uppercase_rejected(self) -> None:
        with pytest.raises(PrivateIPError, match="localhost"):
            await validate_url("https://LOCALHOST/path")

    @pytest.mark.asyncio
    async def test_local_domain_rejected(self) -> None:
        with pytest.raises(PrivateIPError, match=r"\.local"):
            await validate_url("https://myhost.local/path")

    @pytest.mark.asyncio
    async def test_nested_local_domain_rejected(self) -> None:
        with pytest.raises(PrivateIPError, match=r"\.local"):
            await validate_url("https://deep.sub.myhost.local/path")


# ---------------------------------------------------------------------------
# validate_url — domain blocklist
# ---------------------------------------------------------------------------


class TestBlockedDomains:
    """Blocked domains must be rejected without making any HTTP request."""

    @pytest.mark.asyncio
    async def test_blocked_domain_rejected(self) -> None:
        with pytest.raises(BlockedDomainError):
            await validate_url(
                "https://malware.example.com/path",
                blocked_domains=["malware.example.com"],
            )

    @pytest.mark.asyncio
    async def test_blocked_domain_case_insensitive(self) -> None:
        with pytest.raises(BlockedDomainError):
            await validate_url(
                "https://MALWARE.Example.COM/path",
                blocked_domains=["malware.example.com"],
            )

    @pytest.mark.asyncio
    async def test_unblocked_domain_passes(self) -> None:
        with _mock_getaddrinfo(return_value=_fake_addrinfo("93.184.216.34")):
            ip, _ = await validate_url(
                "https://safe.example.com/",
                blocked_domains=["malware.example.com"],
            )
        assert ip == "93.184.216.34"

    @pytest.mark.asyncio
    async def test_empty_blocklist_passes(self) -> None:
        with _mock_getaddrinfo(return_value=_fake_addrinfo("93.184.216.34")):
            ip, _ = await validate_url(
                "https://example.com/",
                blocked_domains=[],
            )
        assert ip == "93.184.216.34"


# ---------------------------------------------------------------------------
# validate_url — edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Malformed URLs, DNS failure, etc."""

    @pytest.mark.asyncio
    async def test_no_hostname_raises_valueerror(self) -> None:
        with pytest.raises(ValueError, match="Unsupported URL scheme"):
            await validate_url("not-a-url")

    @pytest.mark.asyncio
    async def test_unsupported_scheme_raises_valueerror(self) -> None:
        with pytest.raises(ValueError, match="Unsupported URL scheme"):
            await validate_url("ftp://example.com/file")

    @pytest.mark.asyncio
    async def test_dns_failure_raises_valueerror(self) -> None:
        with (
            _mock_getaddrinfo(
                side_effect=socket.gaierror("Name or service not known"),
            ),
            pytest.raises(ValueError, match="DNS resolution failed"),
        ):
            await validate_url("https://nonexistent.example.invalid/")

    @pytest.mark.asyncio
    async def test_empty_addrinfo_raises_valueerror(self) -> None:
        with (
            _mock_getaddrinfo(return_value=[]),
            pytest.raises(ValueError, match="no results"),
        ):
            await validate_url("https://example.com/")


# ---------------------------------------------------------------------------
# validate_url — the fetch-time narrowing (US-003, GOVERNANCE ruling (f))
# ---------------------------------------------------------------------------


class TestFetchTimeNarrowing:
    """The same embedded-address precision, applied at the fetch boundary.

    Each of these was *accepted and fetched* before this story. They are the
    SSRF vectors ruling (f) argues are not the "accepted request value" the
    MAJOR row protects: an IPv6 literal that embeds a private IPv4 is the
    private IPv4 request, spelled so the guard did not recognise it.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "literal",
        [
            "2002:7f00:1::",  # 6to4
            "2001:0:0:0::80ff:fffe",  # Teredo
            "64:ff9b::a00:1",  # NAT64
            "::127.0.0.1",  # IPv4-compatible
            "2001:db8::1",  # Refused before this story too — the list entry
        ],
    )
    async def test_a_private_embedding_is_refused(self, literal: str) -> None:
        with (
            _mock_getaddrinfo(_fake_addrinfo(literal)),
            pytest.raises(PrivateIPError),
        ):
            await validate_url(f"http://[{literal}]/")

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "literal",
        [
            "2002:808:808::",
            "2001:0:0:0::f7f7:f7f7",
            "64:ff9b::808:808",
            "::8.8.8.8",
            "2606:4700::1111",
            # The prefix-guard control: low 32 bits `0.0.32.14`, inside
            # `0.0.0.0/8`. An unguarded mask would refuse a real Google AAAA.
            "2a00:1450:4001:80e::200e",
        ],
    )
    async def test_a_public_embedding_is_still_allowed(self, literal: str) -> None:
        with _mock_getaddrinfo(_fake_addrinfo(literal)):
            resolved, hostname = await validate_url(f"http://[{literal}]/")

        assert resolved == literal
        assert hostname == literal

    @pytest.mark.asyncio
    async def test_a_localhost_suffix_name_is_refused_before_dns(self) -> None:
        """`api.localhost` matched neither blocklist entry before this story."""
        with (
            _mock_getaddrinfo(_fake_addrinfo("93.184.216.34")),
            pytest.raises(PrivateIPError, match="blocked"),
        ):
            await validate_url("http://api.localhost/")
