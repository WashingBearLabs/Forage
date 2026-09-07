"""Tests for Stage 5 -- URL audit, fetch, redirect tracking (US-009/US-003).

All tests mock DNS resolution and HTTP responses — no real network calls.
"""

from __future__ import annotations

import socket
from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from pipeline.stage5_url_audit import (
    DEFAULT_TIMEOUT,
    DEFAULT_USER_AGENTS,
    ContentTooLargeError,
    FetchResult,
    TooManyRedirectsError,
    fetch_url,
)
from url_validator import BlockedDomainError, PrivateIPError

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


def _make_stream_cm(response: httpx.Response) -> MagicMock:
    """Async context manager mock that yields *response* on __aenter__."""
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=response)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _stream_side_effect(*responses: httpx.Response) -> Callable[..., Any]:
    """side_effect for AsyncClient.stream — yields responses in call order."""
    it = iter(responses)

    def _effect(*_args: Any, **_kwargs: Any) -> MagicMock:
        return _make_stream_cm(next(it))

    return _effect


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
        ), patch(
            "httpx.AsyncClient.stream",
            return_value=_make_stream_cm(
                _make_response(
                    content=b"Hello World",
                    headers={"content-type": "text/plain"},
                )
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
        ), patch(
            "httpx.AsyncClient.stream",
            return_value=_make_stream_cm(_make_response()),
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
        ), pytest.raises(PrivateIPError):
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
        ), patch(
            "httpx.AsyncClient.stream",
            return_value=_make_stream_cm(_make_response()),
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
        ), patch(
            "httpx.AsyncClient.stream",
            side_effect=_stream_side_effect(*responses),
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
        ), patch(
            "httpx.AsyncClient.stream",
            side_effect=_stream_side_effect(*responses),
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
        ), patch(
            "httpx.AsyncClient.stream",
            side_effect=_stream_side_effect(*responses),
        ), pytest.raises(TooManyRedirectsError):
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
        ), patch(
            "httpx.AsyncClient.stream",
            side_effect=_stream_side_effect(*responses),
        ), pytest.raises(PrivateIPError):
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
        ), patch(
            "httpx.AsyncClient.stream",
            side_effect=_stream_side_effect(*responses),
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
        ), patch(
            "httpx.AsyncClient.stream",
            side_effect=_stream_side_effect(*responses),
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
        ), patch(
            "httpx.AsyncClient.stream",
            side_effect=_stream_side_effect(*responses),
        ):
            result = await fetch_url("https://example.com/start")

        assert result.domain_changed_on_redirect is False

    @pytest.mark.asyncio
    async def test_no_redirect_no_domain_change(self) -> None:
        with patch(
            "url_validator.socket.getaddrinfo",
            return_value=_fake_addrinfo(),
        ), patch(
            "httpx.AsyncClient.stream",
            return_value=_make_stream_cm(_make_response()),
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
        mock_stream = MagicMock(return_value=_make_stream_cm(_make_response()))
        with patch(
            "url_validator.socket.getaddrinfo",
            return_value=_fake_addrinfo(),
        ), patch("httpx.AsyncClient.stream", mock_stream):
            await fetch_url(
                "https://example.com/",
                user_agents=["TestBot/1.0"],
            )

        call_args = mock_stream.call_args
        assert call_args.kwargs.get("headers", {}).get("User-Agent") == "TestBot/1.0"

    @pytest.mark.asyncio
    async def test_default_ua_from_pool(self) -> None:
        mock_stream = MagicMock(return_value=_make_stream_cm(_make_response()))
        with patch(
            "url_validator.socket.getaddrinfo",
            return_value=_fake_addrinfo(),
        ), patch("httpx.AsyncClient.stream", mock_stream):
            await fetch_url("https://example.com/")

        call_args = mock_stream.call_args
        ua = call_args.kwargs.get("headers", {}).get("User-Agent", "")
        assert ua in DEFAULT_USER_AGENTS

    @pytest.mark.asyncio
    async def test_ua_rotation_randomness(self) -> None:
        """Multiple calls should eventually select different UAs."""
        seen_uas: set[str] = set()
        for _ in range(50):
            mock_stream = MagicMock(
                return_value=_make_stream_cm(_make_response())
            )
            with patch(
                "url_validator.socket.getaddrinfo",
                return_value=_fake_addrinfo(),
            ), patch("httpx.AsyncClient.stream", mock_stream):
                await fetch_url("https://example.com/")
            call_args = mock_stream.call_args
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
        ), patch(
            "pipeline.stage5_url_audit.httpx.AsyncClient",
        ) as mock_client_cls:
            mock_client = MagicMock()
            mock_client.stream.return_value = _make_stream_cm(
                _make_response()
            )
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            await fetch_url("https://example.com/", timeout=15.0)

            call_kwargs = mock_client_cls.call_args
            assert call_kwargs.kwargs["timeout"] == 15.0
            assert call_kwargs.kwargs["follow_redirects"] is False
            assert "verify" in call_kwargs.kwargs  # SSL context

    @pytest.mark.asyncio
    async def test_default_timeout_is_30(self) -> None:
        assert DEFAULT_TIMEOUT == 30.0

    @pytest.mark.asyncio
    async def test_timeout_exception_propagates(self) -> None:
        """httpx.TimeoutException should propagate to caller."""
        timeout_cm = MagicMock()
        timeout_cm.__aenter__ = AsyncMock(
            side_effect=httpx.TimeoutException("timed out")
        )
        timeout_cm.__aexit__ = AsyncMock(return_value=False)
        with patch(
            "url_validator.socket.getaddrinfo",
            return_value=_fake_addrinfo(),
        ), patch(
            "httpx.AsyncClient.stream",
            return_value=timeout_cm,
        ), pytest.raises(httpx.TimeoutException):
            await fetch_url("https://slow.example.com/")


# ---------------------------------------------------------------------------
# Streaming byte-cap (US-003)
# ---------------------------------------------------------------------------


class TestStreamingByteCap:
    """Incremental byte cap must abort oversized responses mid-stream."""

    @pytest.mark.asyncio
    async def test_body_exceeds_cap_raises(self) -> None:
        """Response body exceeding max_content_bytes raises ContentTooLargeError."""
        large_body = b"X" * 200
        with (
            patch("url_validator.socket.getaddrinfo", return_value=_fake_addrinfo()),
            patch(
                "httpx.AsyncClient.stream",
                return_value=_make_stream_cm(_make_response(content=large_body)),
            ),
            pytest.raises(ContentTooLargeError),
        ):
            await fetch_url("https://example.com/", max_content_bytes=100)

    @pytest.mark.asyncio
    async def test_body_exactly_at_limit_succeeds(self) -> None:
        """Body exactly at max_content_bytes must succeed (cap is exclusive)."""
        body = b"A" * 100
        with (
            patch("url_validator.socket.getaddrinfo", return_value=_fake_addrinfo()),
            patch(
                "httpx.AsyncClient.stream",
                return_value=_make_stream_cm(_make_response(content=body)),
            ),
        ):
            result = await fetch_url("https://example.com/", max_content_bytes=100)
        assert result.response_body == body

    @pytest.mark.asyncio
    async def test_content_length_fast_reject(self) -> None:
        """Honest Content-Length over cap is rejected before streaming starts."""
        with (
            patch("url_validator.socket.getaddrinfo", return_value=_fake_addrinfo()),
            patch(
                "httpx.AsyncClient.stream",
                return_value=_make_stream_cm(
                    _make_response(
                        content=b"body",
                        headers={"content-length": "9999999"},
                    )
                ),
            ),
            pytest.raises(ContentTooLargeError, match="Content-Length"),
        ):
            await fetch_url("https://example.com/", max_content_bytes=100)

    @pytest.mark.asyncio
    async def test_redirect_hop_large_body_not_buffered(self) -> None:
        """Redirect hop body is never read — a large redirect body does not OOM.

        The redirect response is exited without calling aiter_bytes(); only
        the Location header is consumed. Setting max_content_bytes very small
        confirms the 1 MB redirect body does not trigger ContentTooLargeError.
        """
        redirect_resp = httpx.Response(
            status_code=301,
            content=b"R" * 1_000_000,  # 1 MB — would fail if buffered
            headers={
                "location": "https://example.com/final",
                "content-type": "text/html",
            },
            request=httpx.Request("GET", "https://example.com/start"),
        )
        final_resp = _make_response(content=b"Final")

        with (
            patch("url_validator.socket.getaddrinfo", return_value=_fake_addrinfo()),
            patch(
                "httpx.AsyncClient.stream",
                side_effect=_stream_side_effect(redirect_resp, final_resp),
            ),
        ):
            result = await fetch_url(
                "https://example.com/start",
                max_content_bytes=50,  # 1 MB redirect would exceed this
            )

        assert result.response_body == b"Final"
        assert result.redirect_chain == ["https://example.com/start"]
        assert result.final_url == "https://example.com/final"

    @pytest.mark.asyncio
    async def test_small_body_within_cap_succeeds(self) -> None:
        """Normal small response is returned correctly."""
        with (
            patch("url_validator.socket.getaddrinfo", return_value=_fake_addrinfo()),
            patch(
                "httpx.AsyncClient.stream",
                return_value=_make_stream_cm(_make_response(content=b"hello")),
            ),
        ):
            result = await fetch_url("https://example.com/", max_content_bytes=1024)
        assert result.response_body == b"hello"
        assert result.status_code == 200


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
