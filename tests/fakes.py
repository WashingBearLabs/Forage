"""Shared test doubles and helpers for the Forage suite."""

from __future__ import annotations

import asyncio
import gc
import hashlib
import os
import socket
import sys
import time
import zlib
from contextlib import contextmanager
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, NoReturn
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from cache import CacheMetrics
from model_fetcher import repo_dirname
from pipeline.search_providers.base import ProviderFailure, ProviderSearchResult

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable, Generator, Mapping
    from types import FrameType

    from models import RetrievedContent


CACHE_HMAC_SENTINEL = "cache-hmac-test-only-" + "x" * 24


class ChunkStream(httpx.AsyncByteStream):
    """Yield and record raw chunks, optionally delaying before each one."""

    def __init__(self, chunks: list[bytes], *, delay: float = 0.0) -> None:
        self._chunks = chunks
        self._delay = delay
        self.chunks_yielded: list[bytes] = []

    @property
    def largest_chunk(self) -> int:
        """Largest raw chunk actually yielded, not a decoded-output bound."""
        return max((len(chunk) for chunk in self.chunks_yielded), default=0)

    async def __aiter__(self) -> AsyncGenerator[bytes]:
        for chunk in self._chunks:
            if self._delay > 0:
                await asyncio.sleep(self._delay)
            self.chunks_yielded.append(chunk)
            yield chunk


def make_response(
    status_code: int = 200,
    content: bytes = b"{}",
    headers: dict[str, str] | None = None,
    *,
    url: str = "https://example.invalid/search",
    content_type: str = "application/json",
) -> httpx.Response:
    """Build a stream-backed response usable by ``aiter_raw`` or ``aiter_bytes``.

    No ``content-length`` is synthesized: tests relying on it must set it.
    ``aiter_bytes`` still decodes this shape, as the stage-5 fetcher expects.
    Each response is single-use, like a real HTTP stream.
    """
    hdrs = {"content-type": content_type}
    if headers:
        hdrs.update(headers)
    return httpx.Response(
        status_code=status_code,
        headers=hdrs,
        stream=ChunkStream([content]),
        request=httpx.Request("GET", url),
    )


def make_stream_cm(response: httpx.Response) -> MagicMock:
    """An async context manager yielding the supplied response."""
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=response)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


@contextmanager
def client_patch(
    target: str,
    *,
    response: httpx.Response | None = None,
    stream_error: Exception | None = None,
) -> Generator[tuple[MagicMock, MagicMock]]:
    """Intercept a per-call streaming client at the caller's patch target."""
    client = MagicMock()
    envelope = response if response is not None else make_response()
    client.stream = MagicMock(
        return_value=make_stream_cm(envelope), side_effect=stream_error
    )
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    with patch(target, return_value=client) as client_cls:
        yield client_cls, client


class RecordingDecompressor:
    """Proxy zlib while recording each output size and every attempted call."""

    def __init__(self, wbits: int) -> None:
        self._decoder = zlib.decompressobj(wbits)
        self.output_sizes: list[int] = []
        self.calls = 0

    def decompress(self, data: bytes, max_length: int = 0) -> bytes:
        self.calls += 1
        out = self._decoder.decompress(data, max_length)
        self.output_sizes.append(len(out))
        return out

    @property
    def largest_output(self) -> int:
        return max(self.output_sizes, default=0)

    @property
    def eof(self) -> bool:
        return self._decoder.eof

    @property
    def unconsumed_tail(self) -> bytes:
        return self._decoder.unconsumed_tail

    @property
    def unused_data(self) -> bytes:
        return self._decoder.unused_data


@dataclass
class DecompressorRecording:
    """Aggregate every decoder, including a raw-deflate retry's new instance."""

    instances: list[RecordingDecompressor] = field(
        default_factory=list[RecordingDecompressor]
    )

    @property
    def largest_output(self) -> int:
        return max((item.largest_output for item in self.instances), default=0)

    @property
    def calls(self) -> int:
        return sum(item.calls for item in self.instances)


@contextmanager
def record_decompressors() -> Generator[DecompressorRecording]:
    """Patch only the bounded reader's factory, never process-global zlib."""
    recording = DecompressorRecording()

    def factory(wbits: int) -> RecordingDecompressor:
        decoder = RecordingDecompressor(wbits)
        recording.instances.append(decoder)
        return decoder

    with patch("pipeline.bounded_body._decompressobj", new=factory):
        yield recording


@dataclass
class DecodedBufferRecording:
    """Simultaneously live decoded payload, not largest decoder return or RSS."""

    accumulation_peak: int = 0
    return_peak: int = 0
    returns: int = 0


@contextmanager
def record_decoded_buffers() -> Generator[DecodedBufferRecording]:
    """Trace the reader's payload owners, outputs and exception-held frames.

    CPython exposes BytesIO's backing bytes via gc.get_referents. Inspecting
    those (without retaining them) neither copies nor exports a buffer view:
    getbuffer() would itself force copy-on-write at getvalue(). Count populated
    payload lengths, as with len(bytearray), not allocator slack/zlib state.
    Deduplicate by buffer identity, including the bytes returned to the caller.
    """
    from pipeline import bounded_body

    recording = DecodedBufferRecording()

    def trace(frame: FrameType, event: str, arg: Any) -> Any:
        if frame.f_code.co_filename != bounded_body.__file__:
            return None
        buffers: dict[int, int] = {}
        visited: set[int] = set()

        def add(value: object) -> None:
            if isinstance(value, BytesIO):
                if not value.closed:
                    for backing in gc.get_referents(value):
                        if isinstance(backing, bytes):
                            buffers[id(backing)] = value.tell()
            elif isinstance(value, bytes | bytearray):
                buffers[id(value)] = len(value)

        def visit(current: FrameType | None) -> None:
            if current is None or id(current) in visited:
                return
            visited.add(id(current))
            if current.f_code.co_filename == bounded_body.__file__:
                state = current.f_locals
                for name in ("body", "output", "result"):
                    add(state.get(name))
                for name in ("self", "decoder"):
                    decoder = state.get(name)
                    if isinstance(decoder, bounded_body._BoundedDecoder):
                        add(getattr(decoder, "body", None))
                # The discarded wrapped decoder can still be held by a caught
                # exception while raw replay is in progress.
                error = state.get("error")
                if isinstance(error, BaseException):
                    traceback = error.__traceback__
                    while traceback is not None:
                        visit(traceback.tb_frame)
                        traceback = traceback.tb_next
            visit(current.f_back)

        visit(frame)
        if event == "return" and isinstance(arg, bytes):
            add(arg)
            recording.return_peak = max(recording.return_peak, sum(buffers.values()))
            recording.returns += 1
        else:
            recording.accumulation_peak = max(
                recording.accumulation_peak, sum(buffers.values())
            )
        return trace

    previous = sys.gettrace()
    sys.settrace(trace)
    try:
        yield recording
    finally:
        sys.settrace(previous)


@dataclass
class RecordingSearchMetrics:
    """The complete ``SearchMetricsSink`` surface, local to each test."""

    fallback_fired: int = 0
    paid_calls: int = 0
    classification_wait_timeouts: int = 0
    provider_compressed_body: int = 0
    provider_timeouts: int = 0
    promptguard_latency_target_exceeded: int = 0
    sanitization_latency_max_ms: int = 0

    @property
    def counters(self) -> dict[str, int]:
        return {
            "fallback_fired": self.fallback_fired,
            "paid_calls": self.paid_calls,
            "classification_wait_timeouts": self.classification_wait_timeouts,
            "provider_compressed_body": self.provider_compressed_body,
            "provider_timeouts": self.provider_timeouts,
            "promptguard_latency_target_exceeded": (
                self.promptguard_latency_target_exceeded
            ),
            "sanitization_latency_max_ms": self.sanitization_latency_max_ms,
        }


def assert_frozen(instance: object, field: str, value: object) -> None:
    """Assert *instance* refuses assignment to *field* — the frozen guarantee.

    The write goes through ``setattr`` with a name the type checker cannot see
    through, and that indirection is the point: written plainly,
    ``instance.field = value`` is a *static* error precisely because the
    dataclass is frozen, so under strict type checking the runtime behaviour
    these tests exist to pin could not be expressed at all. The suite still
    asserts the real thing — that the assignment raises at run time.

    ``AttributeError`` rather than ``dataclasses.FrozenInstanceError`` because
    Forage's result types are ``slots=True`` as well as ``frozen=True``, and
    the two paths raise different subclasses of the same base.
    """
    with pytest.raises(AttributeError):
        setattr(instance, field, value)


def sha256_hex(payload: bytes) -> str:
    """The digest the weights manifest pins, for a fixture's bytes."""
    return hashlib.sha256(payload).hexdigest()


def materialize_hub_snapshot(
    cache_root: Path,
    files: Mapping[str, bytes],
    *,
    model_id: str,
    revision: str,
    symlinks: bool = True,
) -> Path:
    """Build ``hub/models--…/{blobs,snapshots/<rev>}`` the way the hub does.

    ``symlinks=True`` is the real shape — content under ``blobs/``, the
    snapshot a directory of links into it — which is what makes a walk that
    hashes the *link* rather than its target, or one that walks the whole
    model directory, produce the wrong answer. The flat form exists so a test
    can prove the walk handles both.

    Shared because two suites need the same layout for different reasons:
    ``test_model_fetcher.py`` verifies against it, and ``test_app.py``'s
    lifespan tests have ``snapshot_download`` write it.
    """
    repo = cache_root / "hub" / repo_dirname(model_id)
    blobs = repo / "blobs"
    snapshot = repo / "snapshots" / revision
    blobs.mkdir(parents=True, exist_ok=True)
    snapshot.mkdir(parents=True, exist_ok=True)
    for name, payload in files.items():
        link = snapshot / name
        link.parent.mkdir(parents=True, exist_ok=True)
        if symlinks:
            blob = blobs / sha256_hex(payload)
            blob.write_bytes(payload)
            link.symlink_to(os.path.relpath(blob, link.parent))
        else:
            link.write_bytes(payload)
    return snapshot


def hub_download_double(
    files: Mapping[str, bytes],
    *,
    on_call: Callable[[], None] | None = None,
) -> Callable[..., str]:
    """A ``snapshot_download`` stand-in that writes a real cache tree.

    It honours the ``cache_dir`` it is handed, which several assertions rest
    on: a fetcher that wrote somewhere else would materialise a snapshot the
    verifier never looks at, and a double that ignored the argument could not
    tell the two apart.
    """

    def _download(repo_id: str, **kwargs: Any) -> str:
        if on_call is not None:
            on_call()
        cache_dir = Path(str(kwargs["cache_dir"]))
        snapshot = materialize_hub_snapshot(
            cache_dir.parent,
            files,
            model_id=repo_id,
            revision=str(kwargs["revision"]),
        )
        return str(snapshot)

    return _download


def record_network_attempts(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record every outbound attempt made while the returned list is watched.

    The autouse ``pytest-socket`` guard makes a real connection *fail*, which
    is not the same thing as making it *not happen*: ``huggingface_hub``
    swallows every error from its best-effort harness-registry request, so a
    blocked attempt and no attempt at all look identical to a test that only
    asserts the call succeeded. US-005's "zero network on a warm start" is a
    claim about attempts, so the attempts have to be counted.

    This records and then refuses, exactly as the guard does — the recording is
    the only addition. ``AF_UNIX`` sockets are delegated to whatever is already
    installed (asyncio's event-loop self-pipe is one, and the suite allows it).

    Assignment goes through ``monkeypatch`` rather than a bare ``socket.socket
    = …`` for two reasons: restoration is guaranteed even when an assertion
    raises, and re-binding a module's *class* attribute to a function is
    something pyright rightly refuses to type.
    """
    attempts: list[str] = []
    real_socket = socket.socket

    def _refuse(label: str) -> NoReturn:
        attempts.append(label)
        raise OSError(f"network attempt refused by the test recorder: {label}")

    def _socket(
        family: int = socket.AF_INET, *args: Any, **kwargs: Any
    ) -> socket.socket:
        if family in (socket.AF_INET, socket.AF_INET6):
            _refuse(f"socket(family={family})")
        return real_socket(family, *args, **kwargs)

    def _getaddrinfo(host: object, port: object, *args: Any, **kwargs: Any) -> NoReturn:
        _refuse(f"getaddrinfo({host!r}, {port!r})")

    def _create_connection(address: object, *args: Any, **kwargs: Any) -> NoReturn:
        _refuse(f"create_connection({address!r})")

    monkeypatch.setattr(socket, "socket", _socket)
    monkeypatch.setattr(socket, "getaddrinfo", _getaddrinfo)
    monkeypatch.setattr(socket, "create_connection", _create_connection)
    return attempts


def weights_manifest_document(
    files: Mapping[str, bytes],
    *,
    model_id: str,
    revision: str,
    models: Mapping[str, tuple[str, Mapping[str, bytes]]] | None = None,
) -> dict[str, Any]:
    """Build exact-set entries, with optional additional model/revision pairs."""
    return {
        "models": {
            identity: {
                "revision": pin,
                "files": [
                    {"path": name, "sha256": sha256_hex(payload), "size": len(payload)}
                    for name, payload in sorted(contents.items())
                ],
            }
            for identity, (pin, contents) in {
                **(models or {}),
                model_id: (revision, files),
            }.items()
        }
    }


class ManualClock:
    """A monotonic clock a test advances by hand.

    Storage-level TTL expiry is the one behaviour that genuinely differs
    between the backends, and it is the one behaviour a test cannot observe
    without controlling time — ``time.monotonic`` would need the suite to
    actually sleep. Both :class:`FakeStorage` and ``InMemoryStorage`` take the
    clock as a callable for exactly this reason.
    """

    def __init__(self, now: float = 0.0) -> None:
        self._now = now

    def __call__(self) -> float:
        """Return the current fake time, in seconds."""
        return self._now

    def advance(self, seconds: float) -> None:
        """Move the clock forward."""
        self._now += seconds


class FakeStorage:
    """In-test ``CacheStorage``: a dict, a clock, and per-operation call counts.

    Shape-faithful to ``InMemoryStorage`` on everything the policy layer can
    observe — TTL expiry included — and deliberately *unbounded*, so a policy
    test cannot be perturbed by an eviction it did not ask for. The call
    counters are what let a test assert that a policy decision reached (or did
    not reach) the storage at all, which is the assertion the old
    ``mock_redis.set.assert_not_awaited()`` style used to make.
    """

    def __init__(
        self,
        *,
        connected: bool = True,
        metrics: CacheMetrics | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.connected = connected
        self.metrics = metrics if metrics is not None else CacheMetrics()
        self.entries: dict[str, tuple[bytes, float]] = {}
        self.get_calls = 0
        self.set_calls = 0
        self.delete_calls = 0
        self.last_ttl_seconds: int | None = None
        self._clock = clock

    async def connect(self) -> bool:
        """Report the settable ``connected`` state."""
        return self.connected

    async def close(self) -> None:
        """Drop every entry."""
        self.entries.clear()

    async def ping_if_due(self) -> bool:
        """Report the settable ``connected`` state."""
        return self.connected

    async def get(self, key: str) -> bytes | None:
        """Return live bytes for *key*, dropping it once its TTL has passed."""
        self.get_calls += 1
        if not self.connected:
            return None
        entry = self.entries.get(key)
        if entry is None:
            self.metrics.storage_misses += 1
            return None
        value, expires_at = entry
        if expires_at <= self._clock():
            del self.entries[key]
            self.metrics.storage_misses += 1
            return None
        self.metrics.storage_hits += 1
        return value

    async def set(self, key: str, value: str, *, ttl_seconds: int) -> bool:
        """Store *value* under *key* for *ttl_seconds*."""
        self.set_calls += 1
        self.last_ttl_seconds = ttl_seconds
        if not self.connected:
            return False
        self.entries[key] = (value.encode(), self._clock() + ttl_seconds)
        return True

    async def delete(self, key: str) -> bool:
        """Remove *key* if present."""
        self.delete_calls += 1
        if not self.connected:
            return False
        self.entries.pop(key, None)
        return True


class FakeContentCache:
    """Settable-``connected`` double for ``ContentCache`` used by app-level tests.

    ``get``/``put``/``delete`` are no-ops (always miss / never write) so
    fixtures that don't care about cache content can drop this in without
    exercising real Valkey I/O.
    """

    def __init__(self, *, connected: bool = False) -> None:
        self.connected = connected
        self.metrics = CacheMetrics()

    async def ping_if_due(self) -> bool:
        """Report the settable ``connected`` state, mirroring ``ContentCache``."""
        return self.connected

    async def get(
        self,
        url: str,
        *,
        extract_mode: Literal["summary", "full"] = "summary",
        policy_fingerprint: str | None = None,
        ttl_hours: int = 24,
        news_domains: list[str] | None = None,
    ) -> RetrievedContent | None:
        """Always miss."""
        return None

    async def put(
        self,
        url: str,
        content: RetrievedContent,
        *,
        extract_mode: Literal["summary", "full"] = "summary",
        policy_fingerprint: str | None = None,
        ttl_hours: int = 24,
        domain: str = "",
        news_domains: list[str] | None = None,
    ) -> bool:
        """Never write."""
        return False

    async def delete(
        self,
        url: str,
        *,
        extract_mode: Literal["summary", "full"] = "summary",
        policy_fingerprint: str | None = None,
    ) -> bool:
        """Never write."""
        return False

    async def close(self) -> None:
        """No-op; the fake owns no real connection."""
        return None


class FakeSearchProvider:
    """In-test ``SearchProvider``: returns a fixed outcome on every call.

    *outcome* is a :class:`ProviderSearchResult` or :class:`ProviderFailure`
    returned verbatim from ``search()`` (a populated ``ProviderSearchResult``
    with no ``results`` when unset), so a test can drive both the success and
    failure paths of anything that consumes the protocol without touching a
    real backend. Every call is recorded on ``calls`` for assertions about
    what a caller asked for.
    """

    def __init__(
        self,
        *,
        name: str = "fake",
        paid: bool = False,
        origin: str | None = None,
        outcome: ProviderSearchResult | ProviderFailure | None = None,
    ) -> None:
        self.name = name
        self.paid = paid
        self.origin = origin
        self._outcome = outcome
        self.calls: list[tuple[str, int]] = []

    async def search(
        self, query: str, max_results: int
    ) -> ProviderSearchResult | ProviderFailure:
        """Record the call and return the configured *outcome*."""
        self.calls.append((query, max_results))
        if self._outcome is not None:
            return self._outcome
        return ProviderSearchResult(
            provider_name=self.name,
            results=[],
            unresponsive_engines=[],
        )
