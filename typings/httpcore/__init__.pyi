from collections.abc import AsyncIterable, AsyncIterator, Iterable, Mapping
from ssl import SSLContext
from typing import Any

SOCKET_OPTION = (
    tuple[int, int, int]
    | tuple[int, int, bytes | bytearray]
    | tuple[int, int, None, int]
)

TimeoutException: type[Exception]

class NetworkError(Exception): ...
class ProtocolError(Exception): ...
class ProxyError(Exception): ...

UnsupportedProtocol: type[Exception]

def default_ssl_context() -> SSLContext: ...

class AsyncNetworkStream:
    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes: ...
    async def write(self, buffer: bytes, timeout: float | None = None) -> None: ...
    async def aclose(self) -> None: ...
    async def start_tls(
        self,
        ssl_context: SSLContext,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> AsyncNetworkStream: ...
    def get_extra_info(self, info: str) -> Any: ...

class AsyncNetworkBackend:
    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[SOCKET_OPTION] | None = None,
    ) -> AsyncNetworkStream: ...

class AnyIOBackend(AsyncNetworkBackend): ...

class URL:
    def __init__(
        self,
        *,
        scheme: bytes,
        host: bytes,
        port: int | None,
        target: bytes,
    ) -> None: ...

class Request:
    def __init__(
        self,
        method: str,
        url: URL,
        *,
        headers: list[tuple[bytes, bytes]],
        content: AsyncIterable[bytes],
        extensions: Mapping[str, Any],
    ) -> None: ...

class Response:
    status: int
    headers: list[tuple[bytes, bytes]]
    extensions: dict[str, Any]
    def aiter_stream(self) -> AsyncIterator[bytes]: ...
    async def aclose(self) -> None: ...

class AsyncConnectionPool:
    def __init__(
        self,
        *,
        ssl_context: SSLContext,
        max_connections: int,
        max_keepalive_connections: int,
        http1: bool,
        http2: bool,
        network_backend: AsyncNetworkBackend,
    ) -> None: ...
    async def handle_async_request(self, request: Request) -> Response: ...
    async def aclose(self) -> None: ...
