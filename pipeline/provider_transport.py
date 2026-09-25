"""HTTP/1.1 provider reads limited before the next network read occurs."""

from __future__ import annotations

import ssl
from collections.abc import AsyncIterator, Generator, Iterable
from contextlib import contextmanager
from typing import Any

import h11
import httpcore
import httpx

from pipeline.bounded_body import BodyTooLarge


@contextmanager
def _transport_errors() -> Generator[None]:
    try:
        yield
    except httpcore.TimeoutException as exc:
        raise httpx.TimeoutException("provider_timeout") from exc
    except (
        httpcore.NetworkError,
        httpcore.ProtocolError,
        httpcore.ProxyError,
        httpcore.UnsupportedProtocol,
        h11.RemoteProtocolError,
    ) as exc:
        raise httpx.TransportError("provider_transport_error") from exc


class _BudgetedStream(httpcore.AsyncNetworkStream):
    def __init__(self, stream: httpcore.AsyncNetworkStream, limit: int) -> None:
        self._stream = stream
        self._remaining = limit
        self._response_started = False
        self._framing_error: h11.RemoteProtocolError | None = None
        # Observe the same HTTP framing as httpcore, without decoding content.
        # Match its 100 KiB incomplete-header allowance, not h11's 16 KiB default.
        self._observer = h11.Connection(
            h11.CLIENT, max_incomplete_event_size=100 * 1024
        )
        self._observer.send(
            h11.Request(method=b"GET", target=b"/", headers=[(b"Host", b"provider")])
        )
        self._observer.send(h11.EndOfMessage())

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        if self._framing_error is not None:
            raise self._framing_error
        if self._remaining <= 0:
            raise BodyTooLarge()
        data = await self._stream.read(min(max_bytes, self._remaining), timeout=timeout)
        try:
            self._observer.receive_data(data)
            while True:
                event = self._observer.next_event()
                if event is h11.NEED_DATA or event is h11.PAUSED:
                    break
                if isinstance(event, h11.Response):
                    self._response_started = True
                elif isinstance(event, h11.Data):
                    self._remaining -= len(event.data)
                elif isinstance(event, (h11.EndOfMessage, h11.ConnectionClosed)):
                    break
        except h11.RemoteProtocolError as exc:
            if not self._response_started:
                raise
            # Let httpcore expose valid headers before rejecting a coalesced body.
            # No later read is allowed if its parser has not already raised.
            self._framing_error = exc
        return data

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        await self._stream.write(buffer, timeout=timeout)

    async def aclose(self) -> None:
        await self._stream.aclose()

    async def start_tls(
        self,
        ssl_context: ssl.SSLContext,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> httpcore.AsyncNetworkStream:
        self._stream = await self._stream.start_tls(
            ssl_context, server_hostname=server_hostname, timeout=timeout
        )
        return self

    def get_extra_info(self, info: str) -> Any:
        return self._stream.get_extra_info(info)


class _BudgetedBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, backend: httpcore.AsyncNetworkBackend, limit: int) -> None:
        self._backend = backend
        self._limit = limit

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        stream = await self._backend.connect_tcp(
            host,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )
        return _BudgetedStream(stream, self._limit)


class _ResponseStream(httpx.AsyncByteStream):
    def __init__(self, response: httpcore.Response) -> None:
        self._response = response

    async def __aiter__(self) -> AsyncIterator[bytes]:
        with _transport_errors():
            async for chunk in self._response.aiter_stream():
                yield chunk

    async def aclose(self) -> None:
        with _transport_errors():
            await self._response.aclose()


class BoundedProviderTransport(httpx.AsyncBaseTransport):
    """Bound entity bytes at the HTTP/1.1 socket seam, not after rechunking.

    Each provider uses one GET on a fresh connection. If the raw ceiling is
    reached without a complete framed response, refuse without an extra
    overflow-probing read; unknown-length responses cannot prove EOF there.
    """

    def __init__(
        self,
        max_bytes: int,
        *,
        backend: httpcore.AsyncNetworkBackend | None = None,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=(
                ssl_context
                if ssl_context is not None
                else httpcore.default_ssl_context()
            ),
            max_connections=1,
            max_keepalive_connections=0,
            http1=True,
            http2=False,
            network_backend=_BudgetedBackend(
                backend if backend is not None else httpcore.AnyIOBackend(),
                4 * max_bytes,
            ),
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.method != "GET" or not isinstance(
            request.stream, httpx.AsyncByteStream
        ):
            raise ValueError("provider_transport_requires_async_get")
        core_request = httpcore.Request(
            method=request.method,
            url=httpcore.URL(
                scheme=request.url.raw_scheme,
                host=request.url.raw_host,
                port=request.url.port,
                target=request.url.raw_path,
            ),
            headers=request.headers.raw,
            content=request.stream,
            extensions=request.extensions,
        )
        with _transport_errors():
            response = await self._pool.handle_async_request(core_request)
        return httpx.Response(
            response.status,
            headers=response.headers,
            stream=_ResponseStream(response),
            extensions=response.extensions,
        )

    async def aclose(self) -> None:
        await self._pool.aclose()
