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


async def test_wrapped_deflate_filler_stops_at_the_raw_budget() -> None:
    cap = 1000
    # An incomplete stored block also consumes bytes without producing output.
    raw = b"\x78\x9c" + b"\x00\x00\x00\xff\xff" * 799 + b"\x00\x00\x00"
    assert len(raw) == 4 * cap
    stream = ChunkStream([raw[:2], raw[2:], b"unread"])
    response = httpx.Response(
        200, headers={"content-encoding": "deflate"}, stream=stream
    )
    with record_decompressors() as recording, pytest.raises(BodyTooLarge):
        await read_bounded_body(response, max_bytes=cap)
    assert stream.chunks_yielded == [raw[:2], raw[2:]]
    assert recording.largest_output == 0
    assert sum(map(len, stream.chunks_yielded)) == 4 * cap


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


async def test_corruption_after_deflate_header_fails_both_formats() -> None:
    raw = zlib.compress(b"{}")
    response = httpx.Response(
        200,
        headers={"content-encoding": "deflate"},
        stream=ChunkStream([raw[:2], b"corrupt"]),
    )
    with record_decompressors() as recording, pytest.raises(MalformedBody):
        await read_bounded_body(response, max_bytes=100)
    assert len(recording.instances) == 2


@pytest.mark.parametrize("split", [*range(1, 14), None])
async def test_raw_deflate_with_a_valid_zlib_header(split: int | None) -> None:
    raw = bytes.fromhex("780100feff20010200fdff7b7d")
    chunks = (
        [raw[:split], raw[split:]]
        if split is not None
        else [raw[index : index + 1] for index in range(len(raw))]
    )
    stream = ChunkStream(chunks)
    response = httpx.Response(
        200, headers={"content-encoding": "deflate"}, stream=stream
    )
    with record_decompressors() as recording:
        assert await read_bounded_body(response, max_bytes=4) == b" {}"
    assert len(recording.instances) == 2
    assert recording.largest_output <= 5
    assert sum(map(len, stream.chunks_yielded)) == len(raw) <= 16


@pytest.mark.parametrize("chunk_size", [1, 2, 7, 256, 4096])
@pytest.mark.parametrize("overflow", [False, True])
async def test_raw_deflate_retry_discards_speculative_wrapped_output(
    chunk_size: int, overflow: bool
) -> None:
    # The wrapped interpretation emits bytes before rejecting this stored block.
    length = 2561
    raw = (
        b"\x78"
        + length.to_bytes(2, "little")
        + (65535 - length).to_bytes(2, "little")
        + b" " * length
        + bytes.fromhex("010200fdff7b7d")
    )
    expected = b" " * length + b"{}"
    cap = len(expected) - int(overflow)
    chunks = [
        raw[index : index + chunk_size] for index in range(0, len(raw), chunk_size)
    ]
    stream = ChunkStream([*chunks, b"unread"] if overflow else chunks)
    response = httpx.Response(
        200, headers={"content-encoding": "deflate"}, stream=stream
    )
    with record_decompressors() as recording:
        if overflow:
            with pytest.raises(BodyTooLarge):
                await read_bounded_body(response, max_bytes=cap)
        else:
            assert await read_bounded_body(response, max_bytes=cap) == expected
    assert len(recording.instances) == 2
    assert recording.largest_output <= cap + 1
    assert stream.chunks_yielded == chunks
    assert sum(map(len, stream.chunks_yielded)) <= 4 * cap


@pytest.mark.parametrize("chunk_size", [1, 2, 4096])
async def test_raw_deflate_retries_when_wrapped_eof_is_missing(
    chunk_size: int,
) -> None:
    length = 2561
    expected = b" " * (length - 2) + b"{}"
    raw = (
        b"\x78"
        + length.to_bytes(2, "little")
        + (65535 - length).to_bytes(2, "little")
        + expected
        + b"\x03\x00"
    )
    probe = zlib.decompressobj()
    probe.decompress(raw, length + 10)
    assert not probe.eof
    stream = ChunkStream(
        [raw[index : index + chunk_size] for index in range(0, len(raw), chunk_size)]
    )
    response = httpx.Response(
        200, headers={"content-encoding": "deflate"}, stream=stream
    )
    with record_decompressors() as recording:
        assert await read_bounded_body(response, max_bytes=length + 10) == expected
    assert len(recording.instances) == 2
    assert recording.largest_output <= length + 11
    assert response.num_bytes_downloaded == len(raw) <= 4 * (length + 10)


@pytest.mark.parametrize("chunk_size", [1, 2, 4096])
@pytest.mark.parametrize("shape", ["truncated", "trailing", "second-member"])
async def test_raw_deflate_retry_still_requires_exactly_one_member(
    chunk_size: int, shape: str
) -> None:
    raw = bytes.fromhex("780100feff20010200fdff7b7d")
    raw = (
        raw[:-1]
        if shape == "truncated"
        else raw + b"trailing"
        if shape == "trailing"
        else raw + zlib.compress(b"{}", wbits=-zlib.MAX_WBITS)
    )
    response = httpx.Response(
        200,
        headers={"content-encoding": "deflate"},
        stream=ChunkStream(
            [
                raw[index : index + chunk_size]
                for index in range(0, len(raw), chunk_size)
            ]
        ),
    )
    with record_decompressors() as recording, pytest.raises(MalformedBody):
        await read_bounded_body(response, max_bytes=100)
    assert len(recording.instances) == 2
    assert recording.largest_output <= 101


@pytest.mark.parametrize("wbits", [zlib.MAX_WBITS, -zlib.MAX_WBITS])
async def test_deflate_header_split_across_single_byte_chunks(wbits: int) -> None:
    raw = zlib.compress(b"{}", wbits=wbits)
    response = httpx.Response(
        200,
        headers={"content-encoding": "deflate"},
        stream=ChunkStream([raw[index : index + 1] for index in range(len(raw))]),
    )
    with record_decompressors() as recording:
        assert await read_bounded_body(response, max_bytes=100) == b"{}"
    assert len(recording.instances) == (2 if wbits < 0 else 1)
    assert recording.largest_output <= 101


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
