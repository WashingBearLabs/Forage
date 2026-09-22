"""URL validation with RFC1918 rejection and DNS rebinding protection.

Validates that URLs do not resolve to private/reserved IP addresses,
enforces domain blocklists, and provides DNS pinning utilities to
prevent DNS rebinding attacks.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import socket
from collections.abc import Sequence
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv6Address
from typing import Literal
from urllib.parse import urlparse

import idna

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
# RFC 6761 §6.3 reserves the whole ``.localhost`` domain for loopback, so
# ``api.localhost`` is ``localhost`` with a label prepended — it matched
# neither entry before `hardening-search-sanitization` US-003.
_BLOCKED_SUFFIXES = {".local", ".localhost"}

# The two transition prefixes whose low 32 bits are an IPv4 address. Both
# unwraps are guarded by membership: an unguarded ``int(addr) & 0xFFFFFFFF``
# would refuse ordinary public IPv6 whose low 32 bits happen to land in a
# private range (``2a00:1450:4001:80e::200e`` masks to ``0.0.32.14``, inside
# ``0.0.0.0/8`` — a real Google AAAA), and ``64:ff9b::/96`` maps the *whole*
# public IPv4 space, so a blanket list entry would make `validate_url` refuse
# every fetch from an IPv6-only DNS64/NAT64 deployment.
_NAT64_PREFIX = ipaddress.IPv6Network("64:ff9b::/96")
_V4_COMPATIBLE_PREFIX = ipaddress.IPv6Network("::/96")

# A label that is a decimal or ``0x``-hex digit run. A host whose *every*
# label matches is a numeric host: it is an address spelling, not a name, and
# the only spelling this service accepts is a canonical dotted quad.
_NUMERIC_LABEL_RE = re.compile(r"^(?:0[xX][0-9A-Fa-f]*|[0-9]+)$")


# ---------------------------------------------------------------------------
# Host canonicalisation
# ---------------------------------------------------------------------------

HostKind = Literal["ipv6", "ipv4", "name"]
"""What `canonicalize_host` decided a host *is*. It classifies; callers decide."""

HostRejectionReason = Literal["unparseable", "numeric_host", "idna"]
"""Closed, content-free log vocabulary for a host that cannot be canonicalised."""


@dataclass(frozen=True, slots=True)
class CanonicalHost:
    """A host that canonicalised, with the form every comparison uses.

    ``host`` is the ASCII form: the UTS-46 encoding for a name, and for an
    address literal the **raw lower-cased literal exactly as it was written**.
    That distinction is load-bearing rather than incidental --
    ``str(ip_address("2001:0:0:0::f7f7:f7f7"))`` is ``2001::f7f7:f7f7`` and
    ``str(ip_address("::8.8.8.8"))`` is ``::808:808``, so re-serialising the
    address object would silently move ``SearchResult.domain`` for every
    uncompressed spelling a provider happens to return. ``address`` is the
    parsed value the classification compares; ``host`` is the value served.
    """

    host: str
    kind: HostKind
    address: IPv4Address | IPv6Address | None


@dataclass(frozen=True, slots=True)
class HostRejection:
    """A host that cannot be canonicalised, and the closed token saying why.

    The token is a **value** rather than something the caller re-derives: the
    UTS-46 encode is run exactly once per host (a caller that re-ran it to
    learn the reason would be a second call site of a versioned table), and a
    bare ``None`` could not tell an IDNA failure from a numeric host.
    """

    reason: HostRejectionReason


def canonicalize_host(host: str) -> CanonicalHost | HostRejection:
    """Canonicalise *host*, literals first. Never raises, never resolves DNS.

    The order is the security property. An IPv6 literal is recognised
    *structurally*, by its colons, and never reaches the UTS-46 encode --
    the UTS-46 encode raises ``InvalidCodepoint`` on U+003A for every IPv6
    literal, and ``urlsplit`` only ever yields a colon-bearing hostname from a
    real bracketed literal, so the branch is not reachable by an NFKC-mapped
    spelling. Every other host is stripped of exactly one trailing dot,
    lower-cased and UTS-46-encoded, stripped of exactly one trailing dot
    *again* -- the second time against the dot set UTS-46 emits -- and *only
    then* classified as numeric or named: ``①②⑦.⓪.⓪.①``,
    ``127。0。0。1`` and ``localhost。`` are not numeric, and do not match a
    blocklist entry, until UTS-46 has mapped them; both fail-opens a
    classify-before-encode order leaves behind.
    """
    if ":" in host:
        try:
            return CanonicalHost(
                host=host.lower(), kind="ipv6", address=ipaddress.IPv6Address(host)
            )
        except ValueError:
            return HostRejection(reason="unparseable")

    # Exactly one trailing dot: `urlsplit("http://localhost../").hostname` is
    # `localhost..`, and the UTS-46 encode accepts a single root dot
    # unchanged -- so an unguarded strip would serve
    # `localhost.`, which matches neither the exact entry nor a suffix. Any
    # dot left after the strip, and any empty interior label, is the same
    # failure the UTS-46 encode reports for an empty label.
    if host.endswith("."):
        host = host[:-1]
    if ".." in host or host.endswith("."):
        return HostRejection(reason="idna")

    host = host.lower()
    try:
        encoded = idna.encode(host, uts46=True).decode("ascii")
    except idna.IDNAError:
        # Covers `InvalidCodepoint` (an underscore label), "Label too long"
        # and empty labels alike.
        return HostRejection(reason="idna")

    # The same strip again, now against the dot set UTS-46 can *produce*
    # rather than the one ASCII carries. U+3002, U+FF0E and U+FF61 are not
    # `.` going in but are coming out (the UTS-46 encode turns `localhost。`
    # into `localhost.`), so a guard that ran only before the encode let a
    # provider append one to any host in the audit: the empty final label
    # broke the all-labels-numeric test below, `127.0.0.1。` classified as a
    # *name*, and `localhost。` matched neither blocklist entry. Same argument
    # as running the numeric classification after the encode rather than
    # before it -- the check has to be relative to what UTS-46 emits.
    if encoded.endswith("."):
        encoded = encoded[:-1]
    if ".." in encoded or encoded.endswith("."):
        return HostRejection(reason="idna")

    labels = encoded.split(".")
    if all(_NUMERIC_LABEL_RE.match(label) for label in labels):
        # Every label is a digit run, so this is an address spelling. Only a
        # canonical dotted quad is accepted: decimal `2130706433`, octal
        # `0177.0.0.1`, short `127.1`, hex `0x7f000001` and mixed `0x7f.0.0.1`
        # all fail this parse and are rejected rather than passed to the name
        # path, where a resolver would answer 127.0.0.1 for each of them.
        try:
            address = ipaddress.IPv4Address(encoded)
        except ValueError:
            return HostRejection(reason="numeric_host")
        return CanonicalHost(host=encoded, kind="ipv4", address=address)

    return CanonicalHost(host=encoded, kind="name", address=None)


def canonical_host(host: str) -> str | None:
    """Return the canonical ASCII form of *host*, or ``None`` if it has none."""
    result = canonicalize_host(host)
    if isinstance(result, CanonicalHost):
        return result.host
    return None


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def private_address_class(
    addr: IPv4Address | IPv6Address,
) -> Literal["private_literal", "embedded_private"] | None:
    """Classify *addr*, reporting **how** it was reached, or ``None`` if public.

    A ``bool`` cannot say whether an address was private in its own right or
    because it embeds a private IPv4, and that distinction is the log token
    an operator aggregates on. The order matters twice: ``::1`` is named
    before the ``::/96`` unwrap because it also lies inside that prefix and is
    a loopback literal, not an embedding; and every unwrap returns *at once*,
    because falling through to the IPv6 list would let ``::ffff:0:0/96``
    re-block a public IPv4-mapped address.

    The two masked unwraps are prefix-guarded. ISATAP
    (``<prefix>::5efe:a.b.c.d``) is deliberately not unwrapped: its prefix is
    deployment-specific and not enumerable.
    """
    if isinstance(addr, IPv4Address):
        if any(addr in net for net in _PRIVATE_NETWORKS_V4):
            return "private_literal"
        return None

    if addr in (ipaddress.IPv6Address("::"), ipaddress.IPv6Address("::1")):
        return "private_literal"

    embedded: IPv4Address | None = addr.ipv4_mapped
    if embedded is None:
        embedded = addr.sixtofour
    if embedded is None and addr.teredo is not None:
        # `teredo` is `(server, client)` and the client field is the
        # ones-complement of the low 32 bits, so a literal that *looks* like
        # it embeds 127.0.0.1 embeds 128.255.255.254. Only the client is an
        # address the host can be talked into reaching.
        embedded = addr.teredo[1]
    if embedded is None and addr in _NAT64_PREFIX:
        embedded = IPv4Address(int(addr) & 0xFFFFFFFF)
    if embedded is None and addr in _V4_COMPATIBLE_PREFIX:
        # RFC 4291 deprecated `::a.b.c.d`, but `ip_address("::127.0.0.1")`
        # has `ipv4_mapped is None`, `is_private False`, and lies in none of
        # the six `_PRIVATE_NETWORKS_V6` entries -- so it reached the fetch
        # boundary untouched.
        embedded = IPv4Address(int(addr) & 0xFFFFFFFF)
    if embedded is not None:
        if any(embedded in net for net in _PRIVATE_NETWORKS_V4):
            return "embedded_private"
        return None

    if any(addr in net for net in _PRIVATE_NETWORKS_V6):
        return "private_literal"
    return None


def _is_private_ip(ip_str: str) -> bool:
    """Return True if *ip_str* is a private/reserved/loopback address."""
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # Unparseable → treat as unsafe
    return private_address_class(addr) is not None


def _check_hostname_blocklist(hostname: str) -> None:
    """Reject hostnames that are inherently private (localhost, .local)."""
    lower = hostname.lower()
    if lower in _BLOCKED_HOSTNAMES:
        raise PrivateIPError(f"Hostname '{hostname}' is blocked (localhost)")
    for suffix in _BLOCKED_SUFFIXES:
        if lower.endswith(suffix):
            raise PrivateIPError(f"Hostname '{hostname}' is blocked ({suffix} domain)")


def is_blocklisted_hostname(host: str) -> bool:
    """Whether *host* is on the built-in private-name list.

    The public, non-raising form of `_check_hostname_blocklist`, so callers
    outside this module never import an underscored name.
    """
    try:
        _check_hostname_blocklist(host)
    except PrivateIPError:
        return True
    return False


@dataclass(frozen=True, slots=True)
class DomainEntry:
    """Internal normalisation state; policy boundaries carry strings."""

    name: str
    wildcard: bool


def domain_list_bytes(entries: Sequence[str]) -> int:
    """Raw UTF-8 size, including the newline separators between entries.

    JSON may contain lone surrogate escapes. Charge three bytes per surrogate
    for sizing only; host canonicalisation still rejects those invalid entries.
    """
    return sum(
        len(entry.encode("utf-8", errors="surrogatepass")) for entry in entries
    ) + max(0, len(entries) - 1)


def normalize_domain_entries(
    entries: Sequence[str], *, denylist: bool, budget_bytes: int | None
) -> tuple[list[str], int]:
    """Canonicalise policy entries, never truncating a denylist for a budget."""
    normalized: list[str] = []
    dropped = 0
    consumed = 0
    for index, raw in enumerate(entries):
        if not denylist and budget_bytes is not None:
            consumed += domain_list_bytes([raw]) + (1 if index else 0)
            if consumed > budget_bytes:
                dropped += len(entries) - index
                break
        stripped = raw.strip()
        wildcard = stripped.startswith(".")
        canonical = canonicalize_host(stripped[1:] if wildcard else stripped)
        if isinstance(canonical, HostRejection):
            dropped += 1
            continue
        if canonical.kind == "name":
            labels = canonical.host.split(".")
            if (
                not all(labels)
                or len(canonical.host) > 253
                or (len(labels) < 2 and (not denylist or wildcard))
            ):
                dropped += 1
                continue
        elif wildcard:
            dropped += 1
            continue
        entry = DomainEntry(canonical.host, wildcard and not denylist)
        normalized.append(("." if entry.wildcard else "") + entry.name)
    return normalized, dropped


# Matching semantics are a cache-key input: every change must carry a rotation.
def hostname_matches(host: str, entry: str, *, allow_suffix: bool) -> bool:
    """Compare canonical strings, with dot-boundary suffixes only for names."""
    name = entry.removeprefix(".")
    if host == name:
        return True
    if (
        not allow_suffix
        or "." not in name
        or ":" in host
        or ":" in name
        or host.replace(".", "").isdigit()
        or name.replace(".", "").isdigit()
    ):
        return False
    return host.endswith("." + name)


def matched_entry(host: str, entries: Sequence[str]) -> str | None:
    """Return the first canonical allowlist entry matching a canonical host."""
    return next(
        (
            entry
            for entry in entries
            if hostname_matches(host, entry, allow_suffix=entry.startswith("."))
        ),
        None,
    )


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
        Optional canonical denylist; multi-label names also block their subdomains.

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

    canonical = canonicalize_host(hostname)
    if isinstance(canonical, HostRejection):
        raise ValueError(f"Invalid hostname ({canonical.reason})")
    hostname = canonical.host
    _check_hostname_blocklist(hostname)

    if any(
        hostname_matches(hostname, entry, allow_suffix=True)
        for entry in blocked_domains or []
    ):
        raise BlockedDomainError(f"Domain '{hostname}' is on the blocklist")

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
