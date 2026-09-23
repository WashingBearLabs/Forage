"""Hermetic bounded decoding tests; no provider, network, or real waits."""

import gzip
import zlib
from unittest.mock import patch

import httpx
import pytest

from pipeline.bounded_body import (
    BodyTooLarge,
    MalformedBody,
    UnsupportedEncoding,
    read_bounded_body,
)
from tests.fakes import (
    ChunkStream,
    RecordingDecompressor,
    make_response,
    record_decompressors,
)


@pytest.mark.parametrize("encoding", [None, "identity", " GZip ", "deflate", "raw"])
@pytest.mark.parametrize("length", [0, 99, 100])
async def test_decodable_bodies_through_the_exact_bound(
    encoding: str | None, length: int
) -> None:
    body = b"x" * length
    if encoding == " GZip ":
        raw = gzip.compress(body)
    elif encoding == "deflate":
        raw = zlib.compress(body)
    elif encoding == "raw":
        raw = zlib.compress(body, wbits=-zlib.MAX_WBITS)
    else:
        raw = body
    headers = (
        {"content-encoding": "deflate" if encoding == "raw" else encoding}
        if encoding is not None
        else {}
    )
    response = make_response(content=raw, headers=headers)
    with record_decompressors() as recording:
        assert await read_bounded_body(response, max_bytes=100) == body
    assert recording.largest_output <= 101
    if encoding == "raw":
        assert len(recording.instances) == 2


@pytest.mark.parametrize("shape", ["plain", "gzip-bomb", "final-chunk", "raw-filler"])
async def test_overflow_stops_reading_with_bounded_outputs(shape: str) -> None:
    cap = 1000
    headers: dict[str, str] = {}
    if shape == "plain":
        chunks = [b"x" * cap, b"x"]
    elif shape == "gzip-bomb":
        raw = b"x" * (cap * 64)
        compressed = gzip.compress(raw)
        assert len(raw) >= 64 * len(compressed)
        chunks = [compressed]
        headers["content-encoding"] = "gzip"
    elif shape == "final-chunk":
        compressor = zlib.compressobj(wbits=zlib.MAX_WBITS | 16)
        first = compressor.compress(b"x" * cap) + compressor.flush(zlib.Z_SYNC_FLUSH)
        final = compressor.compress(b"x") + compressor.flush()
        chunks = [first, final]
        headers["content-encoding"] = "gzip"
    else:
        chunks = [b"\x00\x00\x00\xff\xff" * 200] * 4
        headers["content-encoding"] = "deflate"
    stream = ChunkStream([*chunks, b"must-not-be-read"])
    response = httpx.Response(200, headers=headers, stream=stream)
    with record_decompressors() as recording, pytest.raises(BodyTooLarge):
        await read_bounded_body(response, max_bytes=cap)
    assert stream.chunks_yielded == chunks
    assert recording.largest_output <= cap + 1
    assert sum(map(len, stream.chunks_yielded)) <= 4 * cap
    if shape == "raw-filler":
        assert recording.largest_output == 0
        assert len(recording.instances) == 2


@pytest.mark.parametrize("encoding", ["br", "zstd", "unknown-secret", "gzip, br", ""])
async def test_encoding_dispatch_precedes_length_and_read(encoding: str) -> None:
    stream = ChunkStream([b"unread"])
    response = httpx.Response(
        200,
        headers={"content-encoding": encoding, "content-length": "9" * 21},
        stream=stream,
    )
    with pytest.raises(UnsupportedEncoding) as caught:
        await read_bounded_body(response, max_bytes=100)
    assert str(caught.value) == "unsupported_encoding"
    assert not stream.chunks_yielded


@pytest.mark.parametrize(
    "encoding,length",
    [("identity", "101"), ("gzip", "401"), ("identity", "0" * 21), ("gzip", "0" * 21)],
)
async def test_announced_raw_overflow_is_refused_before_read(
    encoding: str, length: str
) -> None:
    stream = ChunkStream([b"unread"])
    response = httpx.Response(
        200,
        headers={"content-encoding": encoding, "content-length": length},
        stream=stream,
    )
    with pytest.raises(BodyTooLarge):
        await read_bounded_body(response, max_bytes=100)
    assert not stream.chunks_yielded


@pytest.mark.parametrize("announced", [False, True])
async def test_compressed_raw_length_above_decoded_cap_is_not_a_rejection(
    announced: bool,
) -> None:
    cap = 100
    # A legal gzip header's optional comment consumes raw bytes, not output.
    compressed = gzip.compress(b"{}")
    raw = (
        compressed[:3]
        + b"\x10"
        + compressed[4:10]
        + b"x" * 180
        + b"\0"
        + compressed[10:]
    )
    assert cap < len(raw) <= 4 * cap
    headers = {"content-encoding": "gzip"}
    if announced:
        headers["content-length"] = str(len(raw))
    assert (
        await read_bounded_body(
            make_response(content=raw, headers=headers), max_bytes=cap
        )
        == b"{}"
    )


@pytest.mark.parametrize("length", ["not-a-number", "-1", "10.5"])
async def test_non_digit_content_length_leaves_the_running_bound(length: str) -> None:
    response = make_response(content=b"xxxx", headers={"content-length": length})
    with pytest.raises(BodyTooLarge):
        await read_bounded_body(response, max_bytes=3)


@pytest.mark.parametrize(
    "encoding,chunks",
    [
        ("gzip", [gzip.compress(b"{}")[:-1]]),
        ("deflate", [b"corrupt"]),
        ("gzip", [gzip.compress(b"{}") + gzip.compress(b"{}")]),
        ("gzip", [gzip.compress(b"{}") + b"trailing"]),
        ("gzip", [gzip.compress(b"{}"), b"trailing"]),
        ("gzip", []),
    ],
)
async def test_exactly_one_complete_member_is_required(
    encoding: str, chunks: list[bytes]
) -> None:
    response = httpx.Response(
        200, headers={"content-encoding": encoding}, stream=ChunkStream(chunks)
    )
    with pytest.raises(MalformedBody):
        await read_bounded_body(response, max_bytes=100)


async def test_corruption_after_first_deflate_chunk_does_not_retry_raw() -> None:
    raw = zlib.compress(b"{}")
    response = httpx.Response(
        200,
        headers={"content-encoding": "deflate"},
        stream=ChunkStream([raw[:2], b"corrupt"]),
    )
    with record_decompressors() as recording, pytest.raises(MalformedBody):
        await read_bounded_body(response, max_bytes=100)
    assert len(recording.instances) == 1


async def test_no_progress_tail_cannot_spin() -> None:
    class StalledDecompressor(RecordingDecompressor):
        def decompress(self, data: bytes, max_length: int = 0) -> bytes:
            self.tail = data
            return b""

        @property
        def unconsumed_tail(self) -> bytes:
            return self.tail

    response = make_response(content=b"x", headers={"content-encoding": "gzip"})
    with (
        patch("pipeline.bounded_body._decompressobj", new=StalledDecompressor),
        pytest.raises(MalformedBody),
    ):
        await read_bounded_body(response, max_bytes=100)


@pytest.mark.parametrize(
    "error,token",
    [
        (BodyTooLarge, "body_too_large"),
        (UnsupportedEncoding, "unsupported_encoding"),
        (MalformedBody, "malformed_body"),
    ],
)
def test_exception_messages_are_fixed(error: type[Exception], token: str) -> None:
    assert str(error()) == token
    assert "attacker-header-value" not in str(error())
