"""Real HTTP/1.1 parsing over a size-respecting, socket-free network seam."""

from __future__ import annotations

import asyncio
import gzip
import ssl
import zlib
from collections.abc import Iterable
from unittest.mock import patch

import httpcore
import httpx
import pytest

from pipeline.bounded_body import BodyTooLarge, read_bounded_body
from pipeline.provider_transport import BoundedProviderTransport
from pipeline.search_providers.base import ProviderFailure, ProviderSearchResult
from pipeline.search_providers.brave import BraveApiProvider, BraveSettings
from pipeline.search_providers.searxng import SearxngProvider, SearxngSettings
from tests.fakes import record_decompressors


class NetworkStream(httpcore.AsyncNetworkStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = list(chunks)
        self.read_sizes: list[int] = []
        self.delivered = 0
        self.writes: list[bytes] = []
        self.closed = False
        self.tls = False
        self.error: Exception | None = None
        self.delay = 0.0
        self.timeouts: list[float | None] = []

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        self.read_sizes.append(max_bytes)
        self.timeouts.append(timeout)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        if not self.chunks:
            return b""
        data, remainder = self.chunks[0][:max_bytes], self.chunks[0][max_bytes:]
        if remainder:
            self.chunks[0] = remainder
        else:
            self.chunks.pop(0)
        self.delivered += len(data)
        return data

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        self.writes.append(buffer)

    async def aclose(self) -> None:
        self.closed = True

    async def start_tls(
        self,
        ssl_context: ssl.SSLContext,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> httpcore.AsyncNetworkStream:
        assert ssl_context.verify_mode == ssl.CERT_REQUIRED
        assert ssl_context.check_hostname
        assert server_hostname
        self.tls = True
        return self

    def get_extra_info(self, info: str) -> object:
        return None


class NetworkBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, stream: NetworkStream) -> None:
        self.stream = stream

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        return self.stream


@pytest.mark.parametrize("provider_name", ["searxng", "brave"])
@pytest.mark.parametrize("chunk_size", [1, 8192, 65536])
async def test_large_valid_headers_are_independent_of_fragmentation(
    provider_name: str, chunk_size: int
) -> None:
    body = gzip.compress(b"{}")
    headers = (
        b"HTTP/1.1 200 OK\r\nX-Padding: "
        + b"x" * 30000
        + f"\r\nContent-Encoding: gzip\r\nContent-Length: {len(body)}\r\n\r\n".encode()
    )
    wire = headers + body
    stream = NetworkStream(
        [wire[i : i + chunk_size] for i in range(0, len(wire), chunk_size)]
    )
    provider = (
        SearxngProvider()
        if provider_name == "searxng"
        else BraveApiProvider("sentinel")
    )
    with patch(
        "pipeline.provider_transport.httpcore.AnyIOBackend",
        return_value=NetworkBackend(stream),
    ):
        result = await provider.search("q", 3)
    assert isinstance(result, ProviderSearchResult)
    assert result.compressed
    assert stream.delivered - len(headers) == len(body)
    assert stream.closed


@pytest.mark.parametrize("provider_name", ["searxng", "brave"])
async def test_existing_incomplete_header_limit_is_preserved(
    provider_name: str,
) -> None:
    wire = b"HTTP/1.1 200 OK\r\nX-Padding: " + b"x" * 150000 + b"\r\n\r\n{}"
    stream = NetworkStream([wire[i : i + 8192] for i in range(0, len(wire), 8192)])
    provider = (
        SearxngProvider()
        if provider_name == "searxng"
        else BraveApiProvider("sentinel")
    )
    with patch(
        "pipeline.provider_transport.httpcore.AnyIOBackend",
        return_value=NetworkBackend(stream),
    ):
        result = await provider.search("q", 3)
    assert isinstance(result, ProviderFailure)
    assert result.detail == (
        "connect_error" if provider_name == "searxng" else "transport_error"
    )
    assert 100 * 1024 < stream.delivered <= 100 * 1024 + 8192
    assert stream.closed


@pytest.mark.parametrize("provider_name", ["searxng", "brave"])
@pytest.mark.parametrize("chunking", ["whole", "headers-first", "bytes"])
@pytest.mark.parametrize("outcome", ["rate_limited", "unsupported", "malformed"])
async def test_headers_precede_body_framing_failures(
    provider_name: str, chunking: str, outcome: str
) -> None:
    status = 429 if outcome == "rate_limited" else 200
    encoding = "br" if outcome == "unsupported" else "gzip"
    headers = (
        f"HTTP/1.1 {status} Response\r\nContent-Encoding: {encoding}\r\n"
        "Transfer-Encoding: chunked\r\n\r\n"
    ).encode()
    body = b"not-a-chunk-size\r\nSENSITIVE\r\n"
    wire = headers + body
    chunks = (
        [wire]
        if chunking == "whole"
        else [headers, body]
        if chunking == "headers-first"
        else [wire[i : i + 1] for i in range(len(wire))]
    )
    stream = NetworkStream(chunks)
    provider = (
        SearxngProvider()
        if provider_name == "searxng"
        else BraveApiProvider("sentinel")
    )
    with patch(
        "pipeline.provider_transport.httpcore.AnyIOBackend",
        return_value=NetworkBackend(stream),
    ):
        result = await provider.search("q", 3)
    assert isinstance(result, ProviderFailure)
    assert result.failure_class == (
        "rate_limited" if outcome == "rate_limited" else "hard_error"
    )
    assert result.detail == (
        "http_429"
        if outcome == "rate_limited"
        else "unsupported_encoding"
        if outcome == "unsupported"
        else "connect_error"
        if provider_name == "searxng"
        else "transport_error"
    )
    assert result.compressed
    assert stream.delivered - len(headers) <= 4 * provider.settings.max_response_bytes
    assert stream.closed


@pytest.mark.parametrize("provider_name", ["searxng", "brave"])
@pytest.mark.parametrize("cap,chunk_size", [(1000, 997), (1000, 999), (1048576, 65535)])
async def test_exact_raw_read_budget_with_non_dividing_upstream_chunks(
    provider_name: str, cap: int, chunk_size: int
) -> None:
    raw_limit = 4 * cap
    raw = b"\x00\x00\x00\xff\xff" * ((raw_limit + 2 * chunk_size) // 5 + 1)
    headers = b"HTTP/1.1 200 OK\r\nContent-Encoding: deflate\r\n\r\n"
    stream = NetworkStream(
        [headers] + [raw[i : i + chunk_size] for i in range(0, len(raw), chunk_size)]
    )
    provider = (
        SearxngProvider(settings=SearxngSettings(max_response_bytes=cap))
        if provider_name == "searxng"
        else BraveApiProvider("sentinel", BraveSettings(max_response_bytes=cap))
    )
    with (
        patch(
            "pipeline.provider_transport.httpcore.AnyIOBackend",
            return_value=NetworkBackend(stream),
        ),
        record_decompressors() as recording,
    ):
        result = await provider.search("q", 3)
    assert isinstance(result, ProviderFailure)
    assert (result.failure_class, result.detail) == ("hard_error", "body_too_large")
    assert result.compressed
    assert recording.largest_output == 0
    assert stream.delivered - len(headers) == raw_limit
    assert 0 < stream.read_sizes[-1] < chunk_size
    assert stream.closed


@pytest.mark.parametrize("encoding", ["identity", "gzip", "deflate", "raw"])
@pytest.mark.parametrize("framing", ["length", "chunked", "close"])
@pytest.mark.parametrize("chunk_size", [1, 7, 65536])
async def test_framing_is_not_charged_to_the_body_budget(
    encoding: str, framing: str, chunk_size: int
) -> None:
    body = b'{"padding":"' + b"x" * 986 + b'"}'
    raw = (
        body
        if encoding == "identity"
        else gzip.compress(body)
        if encoding == "gzip"
        else zlib.compress(
            body, wbits=-zlib.MAX_WBITS if encoding == "raw" else zlib.MAX_WBITS
        )
    )
    content_encoding = "deflate" if encoding == "raw" else encoding
    headers = f"HTTP/1.1 200 OK\r\nContent-Encoding: {content_encoding}\r\n".encode()
    if framing == "length":
        headers += f"Content-Length: {len(raw)}\r\n".encode()
        framed = raw
    elif framing == "chunked":
        headers += b"Transfer-Encoding: chunked\r\n"
        framed = b"".join(b"1\r\n" + raw[i : i + 1] + b"\r\n" for i in range(len(raw)))
        framed += b"0\r\nX-Trailer: complete\r\n\r\n"
    else:
        framed = raw
    wire = headers + b"\r\n" + framed
    stream = NetworkStream(
        [wire[i : i + chunk_size] for i in range(0, len(wire), chunk_size)]
    )
    transport = BoundedProviderTransport(1000, backend=NetworkBackend(stream))
    async with (
        httpx.AsyncClient(transport=transport, trust_env=False) as client,
        client.stream("GET", "https://provider.test/") as response,
    ):
        assert await read_bounded_body(response, max_bytes=1000) == body
        assert response.num_bytes_downloaded == len(raw)
    assert stream.delivered == len(wire)
    assert stream.closed and stream.tls


async def test_declared_raw_ceiling_can_finish_without_an_extra_read() -> None:
    raw = b"x" * 4000
    headers = b"HTTP/1.1 200 OK\r\nContent-Length: 4000\r\n\r\n"
    stream = NetworkStream([headers, raw])
    transport = BoundedProviderTransport(1000, backend=NetworkBackend(stream))
    async with httpx.AsyncClient(transport=transport) as client:
        response = await client.get("http://provider.test/")
    assert response.content == raw
    assert stream.delivered - len(headers) == 4000
    assert stream.closed


@pytest.mark.parametrize("provider_name", ["searxng", "brave"])
@pytest.mark.parametrize(
    "failure",
    ["timeout", "network", "protocol", "unsupported", "overflow", "success", "slow"],
)
async def test_provider_mapping_and_closure_over_the_real_transport(
    provider_name: str, failure: str, caplog: pytest.LogCaptureFixture
) -> None:
    cap = 1000
    body = b'{"web":{"results":[]}}'
    headers = b"HTTP/1.1 200 OK\r\nContent-Length: 22\r\n\r\n"
    stream = NetworkStream([headers, body])
    if failure == "timeout":
        stream.error = httpcore.TimeoutException("SENSITIVE")
    elif failure == "network":
        stream.error = httpcore.NetworkError("SENSITIVE")
    elif failure == "protocol":
        stream.chunks = [b"not-http\r\nSENSITIVE\r\n\r\n"]
    elif failure == "unsupported":
        stream.chunks = [
            b"HTTP/1.1 200 OK\r\nContent-Encoding: SENSITIVE\r\n\r\n",
            body,
        ]
    elif failure == "overflow":
        stream.chunks = [
            b"HTTP/1.1 200 OK\r\nContent-Length: 1001\r\n\r\n",
            b"x" * 1001,
        ]
    elif failure == "slow":
        stream.delay = 0.025
    provider = (
        SearxngProvider(
            settings=SearxngSettings(
                max_response_bytes=cap,
                timeout_seconds=0.01 if failure == "slow" else 1.0,
            )
        )
        if provider_name == "searxng"
        else BraveApiProvider(
            "SENSITIVE",
            BraveSettings(
                max_response_bytes=cap,
                timeout_seconds=0.01 if failure == "slow" else 1.0,
            ),
        )
    )
    with patch(
        "pipeline.provider_transport.httpcore.AnyIOBackend",
        return_value=NetworkBackend(stream),
    ):
        result = await provider.search("q", 3)
    if failure == "success":
        assert isinstance(result, ProviderSearchResult)
    else:
        assert isinstance(result, ProviderFailure)
        assert (
            result.detail
            == {
                "timeout": "timeout",
                "slow": "timeout",
                "network": (
                    "connect_error" if provider_name == "searxng" else "transport_error"
                ),
                "protocol": (
                    "connect_error" if provider_name == "searxng" else "transport_error"
                ),
                "unsupported": "unsupported_encoding",
                "overflow": "body_too_large",
            }[failure]
        )
        if failure in {"unsupported", "overflow"}:
            assert len(stream.chunks) == 1
    assert stream.closed
    assert "SENSITIVE" not in caplog.text
    assert stream.timeouts and set(stream.timeouts) == {
        provider.settings.timeout_seconds
    }


async def test_unknown_length_at_ceiling_refuses_instead_of_probing_overflow() -> None:
    headers = b"HTTP/1.1 200 OK\r\n\r\n"
    stream = NetworkStream([headers, b"x" * 4000, b"overflow"])
    transport = BoundedProviderTransport(1000, backend=NetworkBackend(stream))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(BodyTooLarge):
            await client.get("http://provider.test/")
    assert stream.delivered - len(headers) == 4000
    assert stream.closed
