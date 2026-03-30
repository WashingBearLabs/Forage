"""Tests for Stage 5 -- URL audit, fetch, redirect tracking (US-009).

All tests mock DNS resolution and HTTP responses — no real network calls.
"""

from __future__ import annotations

import socket
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

# Add the retrieval service root to sys.path so modules are importable
_retrieval_root = str(
    Path(__file__).resolve().parents[2] / "services" / "retrieval"
)
if _retrieval_root not in sys.path:
    sys.path.insert(0, _retrieval_root)

from pipeline.stage5_url_audit import (  # noqa: E402
    DEFAULT_TIMEOUT,
    DEFAULT_USER_AGENTS,
    FetchResult,
    TooManyRedirectsError,
    fetch_url,
)
from url_validator import BlockedDomainError, PrivateIPError  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_addrinfo(ip: str = "93.184.216.34") -> list[tuple]:
    """Build a minimal getaddrinfo result returning a single public IP."""
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    return [(family, socket.SOCK_STREAM, 0, "", (ip, 0))]


def _make_response(
    status_code: int = 200,
    content: bytes = b"<html>OK</html>",
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """Build a minimal httpx.Response."""
    hdrs = {"content-type": "text/html; charset=utf-8"}
    if headers:
        hdrs.update(headers)
    return httpx.Response(
        status_code=status_code,
        content=content,
        headers=hdrs,
        request=httpx.Request("GET", "https://example.com"),
    )


def _make_redirect(location: str, status_code: int = 301) -> httpx.Response:
    """Build a redirect response with a Location header."""
    return _make_response(
        status_code=status_code,
        content=b"",
        headers={"location": location},
    )


# ---------------------------------------------------------------------------
# Basic fetch — no redirects
# ---------------------------------------------------------------------------


class TestBasicFetch:
    """Simple URL fetch with no redirects."""

    @pytest.mark.asyncio
    async def test_successful_fetch(self) -> None:
        with patch(
            "url_validator.socket.getaddrinfo",
            return_value=_fake_addrinfo(),
        ):
            with patch(
                "httpx.AsyncClient.get",
                new_callable=AsyncMock,
                return_value=_make_response(
                    content=b"Hello World",
                    headers={"content-type": "text/plain"},
                ),
            ):
                result = await fetch_url("https://example.com/page")

        assert result.final_url == "https://example.com/page"
        assert result.redirect_chain == []
        assert result.domain_changed_on_redirect is False
        assert result.response_body == b"Hello World"
        assert result.content_type == "text/plain"
        assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_result_is_frozen(self) -> None:
        with patch(
            "url_validator.socket.getaddrinfo",
            return_value=_fake_addrinfo(),
        ):
            with patch(
                "httpx.AsyncClient.get",
                new_callable=AsyncMock,
                return_value=_make_response(),
            ):
                result = await fetch_url("https://example.com/")

        with pytest.raises(AttributeError):
            result.final_url = "https://other.com"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# RFC1918 rejection during fetch
# ---------------------------------------------------------------------------


class TestRFC1918DuringFetch:
    """Private IPs must be rejected before any HTTP request."""

    @pytest.mark.asyncio
    async def test_private_ip_rejected(self) -> None:
        with patch(
            "url_validator.socket.getaddrinfo",
            return_value=_fake_addrinfo("10.0.0.1"),
        ):
            with pytest.raises(PrivateIPError):
                await fetch_url("https://internal.example.com/")

    @pytest.mark.asyncio
    async def test_localhost_rejected(self) -> None:
        with pytest.raises(PrivateIPError, match="localhost"):
            await fetch_url("https://localhost/secret")

    @pytest.mark.asyncio
    async def test_local_domain_rejected(self) -> None:
        with pytest.raises(PrivateIPError, match=r"\.local"):
            await fetch_url("https://myserver.local/admin")


# ---------------------------------------------------------------------------
# Domain blocklist
# ---------------------------------------------------------------------------


class TestBlocklistDuringFetch:
    """Blocked domains must be rejected without HTTP requests."""

    @pytest.mark.asyncio
    async def test_blocked_domain_rejected(self) -> None:
        with pytest.raises(BlockedDomainError):
            await fetch_url(
                "https://malware.example.com/payload",
                blocked_domains=["malware.example.com"],
            )

    @pytest.mark.asyncio
    async def test_unblocked_domain_succeeds(self) -> None:
        with patch(
            "url_validator.socket.getaddrinfo",
            return_value=_fake_addrinfo(),
        ):
            with patch(
                "httpx.AsyncClient.get",
                new_callable=AsyncMock,
                return_value=_make_response(),
            ):
                result = await fetch_url(
                    "https://safe.example.com/",
                    blocked_domains=["malware.example.com"],
                )
        assert result.status_code == 200


# ---------------------------------------------------------------------------
# Redirect tracking
# ---------------------------------------------------------------------------


class TestRedirectTracking:
    """Redirect chains must be tracked and each hop validated."""

    @pytest.mark.asyncio
    async def test_single_redirect(self) -> None:
        responses = [
            _make_redirect("https://example.com/final"),
            _make_response(content=b"Final page"),
        ]
        with patch(
            "url_validator.socket.getaddrinfo",
            return_value=_fake_addrinfo(),
        ):
            with patch(
                "httpx.AsyncClient.get",
                new_callable=AsyncMock,
                side_effect=responses,
            ):
                result = await fetch_url("https://example.com/start")

        assert result.redirect_chain == ["https://example.com/start"]
        assert result.final_url == "https://example.com/final"
        assert result.response_body == b"Final page"
        assert result.domain_changed_on_redirect is False

    @pytest.mark.asyncio
    async def test_multiple_redirects(self) -> None:
        responses = [
            _make_redirect("https://example.com/hop2"),
            _make_redirect("https://example.com/hop3"),
            _make_response(content=b"Done"),
        ]
        with patch(
            "url_validator.socket.getaddrinfo",
            return_value=_fake_addrinfo(),
        ):
            with patch(
                "httpx.AsyncClient.get",
                new_callable=AsyncMock,
                side_effect=responses,
            ):
                result = await fetch_url("https://example.com/hop1")

        assert result.redirect_chain == [
            "https://example.com/hop1",
            "https://example.com/hop2",
        ]
        assert result.final_url == "https://example.com/hop3"

    @pytest.mark.asyncio
    async def test_too_many_redirects(self) -> None:
        """More redirects than max_redirects should raise."""
        responses = [
            _make_redirect(f"https://example.com/hop{i}")
            for i in range(10)
        ]
        with patch(
            "url_validator.socket.getaddrinfo",
            return_value=_fake_addrinfo(),
        ):
            with patch(
                "httpx.AsyncClient.get",
                new_callable=AsyncMock,
                side_effect=responses,
            ):
                with pytest.raises(TooManyRedirectsError):
                    await fetch_url(
                        "https://example.com/start", max_redirects=3,
                    )

    @pytest.mark.asyncio
    async def test_redirect_to_private_ip_rejected(self) -> None:
        """Redirect destination resolving to private IP must be rejected."""
        call_count = 0

        def _varying_addrinfo(host, port):
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                # First call (original URL) — public
                return _fake_addrinfo("93.184.216.34")
            # Second call (redirect destination) — private
            return _fake_addrinfo("10.0.0.1")

        responses = [
            _make_redirect("https://evil.internal/admin"),
        ]
        with patch(
            "url_validator.socket.getaddrinfo",
            side_effect=_varying_addrinfo,
        ):
            with patch(
                "httpx.AsyncClient.get",
                new_callable=AsyncMock,
                side_effect=responses,
            ):
                with pytest.raises(PrivateIPError):
                    await fetch_url("https://legit.example.com/")

    @pytest.mark.asyncio
    async def test_relative_redirect_resolved(self) -> None:
        """Relative Location headers must be resolved against current URL."""
        responses = [
            _make_redirect("/new-path"),
            _make_response(content=b"Resolved"),
        ]
        with patch(
            "url_validator.socket.getaddrinfo",
            return_value=_fake_addrinfo(),
        ):
            with patch(
                "httpx.AsyncClient.get",
                new_callable=AsyncMock,
                side_effect=responses,
            ):
                result = await fetch_url("https://example.com/old-path")

        assert result.final_url == "https://example.com/new-path"
        assert result.redirect_chain == ["https://example.com/old-path"]


# ---------------------------------------------------------------------------
# Domain change detection
# ---------------------------------------------------------------------------


class TestDomainChangeDetection:
    """Domain changes on redirect must be flagged."""

    @pytest.mark.asyncio
    async def test_domain_change_flagged(self) -> None:
        responses = [
            _make_redirect("https://other-domain.com/page"),
            _make_response(content=b"New domain"),
        ]
        with patch(
            "url_validator.socket.getaddrinfo",
            return_value=_fake_addrinfo(),
        ):
            with patch(
                "httpx.AsyncClient.get",
                new_callable=AsyncMock,
                side_effect=responses,
            ):
                result = await fetch_url("https://example.com/start")

        assert result.domain_changed_on_redirect is True
        assert result.final_url == "https://other-domain.com/page"

    @pytest.mark.asyncio
    async def test_same_domain_not_flagged(self) -> None:
        responses = [
            _make_redirect("https://example.com/other-page"),
            _make_response(content=b"Same domain"),
        ]
        with patch(
            "url_validator.socket.getaddrinfo",
            return_value=_fake_addrinfo(),
        ):
            with patch(
                "httpx.AsyncClient.get",
                new_callable=AsyncMock,
                side_effect=responses,
            ):
                result = await fetch_url("https://example.com/start")

        assert result.domain_changed_on_redirect is False

    @pytest.mark.asyncio
    async def test_no_redirect_no_domain_change(self) -> None:
        with patch(
            "url_validator.socket.getaddrinfo",
            return_value=_fake_addrinfo(),
        ):
            with patch(
                "httpx.AsyncClient.get",
                new_callable=AsyncMock,
                return_value=_make_response(),
            ):
                result = await fetch_url("https://example.com/")

        assert result.domain_changed_on_redirect is False


# ---------------------------------------------------------------------------
# User-Agent rotation
# ---------------------------------------------------------------------------


class TestUserAgentRotation:
    """User-Agent must be selected from the configured pool."""

    @pytest.mark.asyncio
    async def test_custom_ua_used(self) -> None:
        mock_get = AsyncMock(return_value=_make_response())
        with patch(
            "url_validator.socket.getaddrinfo",
            return_value=_fake_addrinfo(),
        ):
            with patch("httpx.AsyncClient.get", mock_get):
                await fetch_url(
                    "https://example.com/",
                    user_agents=["TestBot/1.0"],
                )

        call_args = mock_get.call_args
        assert call_args.kwargs.get("headers", {}).get("User-Agent") == "TestBot/1.0"

    @pytest.mark.asyncio
    async def test_default_ua_from_pool(self) -> None:
        mock_get = AsyncMock(return_value=_make_response())
        with patch(
            "url_validator.socket.getaddrinfo",
            return_value=_fake_addrinfo(),
        ):
            with patch("httpx.AsyncClient.get", mock_get):
                await fetch_url("https://example.com/")

        call_args = mock_get.call_args
        ua = call_args.kwargs.get("headers", {}).get("User-Agent", "")
        assert ua in DEFAULT_USER_AGENTS

    @pytest.mark.asyncio
    async def test_ua_rotation_randomness(self) -> None:
        """Multiple calls should eventually select different UAs."""
        seen_uas: set[str] = set()
        for _ in range(50):
            mock_get = AsyncMock(return_value=_make_response())
            with patch(
                "url_validator.socket.getaddrinfo",
                return_value=_fake_addrinfo(),
            ):
                with patch("httpx.AsyncClient.get", mock_get):
                    await fetch_url("https://example.com/")
            call_args = mock_get.call_args
            ua = call_args.kwargs.get("headers", {}).get("User-Agent", "")
            seen_uas.add(ua)

        # With 5 default UAs and 50 calls, we should see at least 2
        assert len(seen_uas) >= 2


# ---------------------------------------------------------------------------
# Timeout enforcement
# ---------------------------------------------------------------------------


class TestTimeoutEnforcement:
    """Per-request timeout must be enforced."""

    @pytest.mark.asyncio
    async def test_timeout_propagated(self) -> None:
        """httpx.AsyncClient should be created with the specified timeout."""
        with patch(
            "url_validator.socket.getaddrinfo",
            return_value=_fake_addrinfo(),
        ):
            with patch(
                "pipeline.stage5_url_audit.httpx.AsyncClient",
            ) as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.get = AsyncMock(return_value=_make_response())
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=False)
                mock_client_cls.return_value = mock_client

                await fetch_url("https://example.com/", timeout=15.0)

                mock_client_cls.assert_called_once_with(
                    timeout=15.0, follow_redirects=False,
                )

    @pytest.mark.asyncio
    async def test_default_timeout_is_30(self) -> None:
        assert DEFAULT_TIMEOUT == 30.0

    @pytest.mark.asyncio
    async def test_timeout_exception_propagates(self) -> None:
        """httpx.TimeoutException should propagate to caller."""
        with patch(
            "url_validator.socket.getaddrinfo",
            return_value=_fake_addrinfo(),
        ):
            with patch(
                "httpx.AsyncClient.get",
                new_callable=AsyncMock,
                side_effect=httpx.TimeoutException("timed out"),
            ):
                with pytest.raises(httpx.TimeoutException):
                    await fetch_url("https://slow.example.com/")


# ---------------------------------------------------------------------------
# FetchResult dataclass structure
# ---------------------------------------------------------------------------


class TestFetchResultStructure:
    """Verify FetchResult dataclass shape and defaults."""

    def test_frozen(self) -> None:
        result = FetchResult(final_url="https://example.com")
        with pytest.raises(AttributeError):
            result.final_url = "other"  # type: ignore[misc]

    def test_defaults(self) -> None:
        result = FetchResult(final_url="https://example.com")
        assert result.redirect_chain == []
        assert result.domain_changed_on_redirect is False
        assert result.response_body == b""
        assert result.content_type == ""
        assert result.status_code == 0
