"""Contract tests for the shared streaming, classifier and metrics doubles."""

from __future__ import annotations

import gzip
import time
import zlib
from contextlib import AbstractAsyncContextManager
from typing import cast, get_type_hints

import httpx
import pytest

from pipeline import bounded_body
from pipeline.orchestrator import SearchMetricsSink
from tests.fakes import (
    ChunkStream,
    RecordingDecompressor,
    RecordingSearchMetrics,
    client_patch,
    make_mock_classifier,
    make_response,
    make_stream_cm,
    record_decompressors,
)


@pytest.mark.parametrize(
    ("score", "chunks", "loaded"),
    [(0.0, None, True), (0.95, ["a", "b"], True), (0.0, None, False)],
)
def test_mock_classifier_exposes_windows_and_real_pooling(
    score: float, chunks: list[str] | None, loaded: bool
) -> None:
    classifier = make_mock_classifier(score, chunks, loaded)
    expected_chunks = chunks or ["input"]
    assert classifier.loaded is loaded
    assert classifier.classify_windows("input", max_chunks=3) == (
        [score] * len(expected_chunks),
        expected_chunks,
    )
    classifier.classify_windows.reset_mock()
    assert classifier.classify("input", max_chunks=3) == (score, expected_chunks)
    classifier.classify_windows.assert_called_once_with("input", max_chunks=3)


def test_mock_classifier_overrides_the_window_seam_for_both_entrypoints() -> None:
    classifier = make_mock_classifier()
    classifier.classify_windows.side_effect = None
    classifier.classify_windows.return_value = ([0.1, 0.9, 0.9], ["a", "b", "c"])
    assert classifier.classify("input") == (0.9, ["b", "c"])
    classifier.classify_windows.assert_called_once_with("input", max_chunks=None)
    classifier.classify_windows.side_effect = ValueError("synthetic failure")
    with pytest.raises(ValueError, match="synthetic failure"):
        classifier.classify("input")


async def test_chunk_stream_records_only_raw_chunks_actually_yielded() -> None:
    stream = ChunkStream([b"x", b"xyz", b"not-yet-yielded"])
    assert stream.largest_chunk == 0
    assert stream.chunks_yielded == []
    iterator = stream.__aiter__()
    assert await anext(iterator) == b"x"
    assert await anext(iterator) == b"xyz"
    assert stream.chunks_yielded == [b"x", b"xyz"]
    assert stream.largest_chunk == 3
    await iterator.aclose()


async def test_chunk_stream_delays_before_every_chunk() -> None:
    stream = ChunkStream([b"a", b"b"], delay=0.05)
    start = time.monotonic()
    assert [chunk async for chunk in stream] == [b"a", b"b"]
    assert time.monotonic() - start >= 0.1
    assert stream.chunks_yielded == [b"a", b"b"]


async def test_response_defaults_support_raw_and_decoded_reads_without_length() -> None:
    raw_response = make_response()
    assert raw_response.headers.get("content-length") is None
    assert raw_response.headers["content-type"] == "application/json"
    assert raw_response.request.url == "https://example.invalid/search"
    assert [chunk async for chunk in raw_response.aiter_raw()] == [b"{}"]

    decoded_response = make_response()
    assert decoded_response.headers.get("content-length") is None
    assert [chunk async for chunk in decoded_response.aiter_bytes()] == [b"{}"]


async def test_response_preserves_raw_compression_and_allows_httpx_decoding() -> None:
    payload = b"decoded text" * 100
    compressed = gzip.compress(payload)
    headers = {"content-encoding": "gzip", "content-length": str(len(compressed))}
    raw = make_response(200, compressed, headers)
    decoded = make_response(200, compressed, headers)
    assert b"".join([chunk async for chunk in raw.aiter_raw()]) == compressed
    assert b"".join([chunk async for chunk in decoded.aiter_bytes()]) == payload
    assert raw.headers["content-length"] == str(len(compressed))


async def test_response_accepts_stage5_defaults_and_explicit_overrides() -> None:
    response = make_response(
        201,
        b"<html>OK</html>",
        {"x-test": "present"},
        url="https://example.com",
        content_type="text/html; charset=utf-8",
    )
    response.raise_for_status()
    assert response.status_code == 201
    assert response.headers["content-type"] == "text/html; charset=utf-8"
    assert response.headers["x-test"] == "present"
    assert response.request.url == "https://example.com"
    assert await response.aread() == b"<html>OK</html>"
    overridden = make_response(headers={"content-type": "text/plain"})
    assert overridden.headers["content-type"] == "text/plain"


async def test_stream_context_yields_response_and_propagates_errors() -> None:
    response = make_response()
    cm = make_stream_cm(response)
    with pytest.raises(ValueError, match="test error"):
        async with cast("AbstractAsyncContextManager[httpx.Response]", cm) as actual:
            assert actual is response
            raise ValueError("test error")
    cm.__aenter__.assert_awaited_once()
    cm.__aexit__.assert_awaited_once()


@pytest.mark.parametrize("supplied", [False, True])
async def test_client_patch_uses_target_and_supplied_or_default_response(
    supplied: bool,
) -> None:
    original = httpx.AsyncClient
    response = make_response(content=b"supplied") if supplied else None
    with client_patch("tests.fakes.httpx.AsyncClient", response=response) as (
        client_cls,
        client,
    ):
        async with httpx.AsyncClient(timeout=30) as actual:
            assert actual is client
            async with actual.stream("GET", "https://example.invalid") as envelope:
                if supplied:
                    assert envelope is response
                assert await envelope.aread() == (b"supplied" if supplied else b"{}")
        client_cls.assert_called_once_with(timeout=30)
        client.__aexit__.assert_awaited_once()
    assert httpx.AsyncClient is original


async def test_client_patch_can_raise_a_stream_error_and_restores_target() -> None:
    original = httpx.AsyncClient
    error = httpx.ReadTimeout("synthetic timeout")
    with client_patch("tests.fakes.httpx.AsyncClient", stream_error=error) as (
        _,
        client,
    ):
        with pytest.raises(httpx.ReadTimeout) as caught:
            async with httpx.AsyncClient() as actual:
                async with actual.stream("GET", "https://example.invalid"):
                    pytest.fail("The stream must fail")
        assert caught.value is error
        client.__aexit__.assert_awaited_once()
    assert httpx.AsyncClient is original


def test_recording_decompressor_proxies_bounded_outputs_and_stream_state() -> None:
    decoder = RecordingDecompressor(zlib.MAX_WBITS)
    compressed = zlib.compress(b"x" * 100)
    assert decoder.calls == decoder.largest_output == 0
    assert decoder.decompress(compressed + b"tail", max_length=7) == b"x" * 7
    assert decoder.unconsumed_tail
    assert not decoder.eof
    assert decoder.decompress(decoder.unconsumed_tail, max_length=100) == b"x" * 93
    assert decoder.eof
    assert decoder.unused_data == b"tail"
    assert decoder.output_sizes == [7, 93]
    assert decoder.calls == 2
    assert decoder.largest_output == 93
    assert RecordingDecompressor(zlib.MAX_WBITS).largest_output == 0


def test_recorder_aggregates_every_instance_without_patching_global_zlib() -> None:
    original = bounded_body._decompressobj
    with record_decompressors() as recording:
        assert zlib.decompressobj is original
        assert recording.instances == []
        assert recording.calls == recording.largest_output == 0
        first = bounded_body._decompressobj(zlib.MAX_WBITS)
        second = bounded_body._decompressobj(zlib.MAX_WBITS)
        assert first.decompress(zlib.compress(b"a" * 80)) == b"a" * 80
        assert second.decompress(zlib.compress(b"b" * 20)) == b"b" * 20
        assert len(recording.instances) == 2
        assert recording.calls == 2
        assert recording.largest_output == 80
    assert bounded_body._decompressobj is original
    with record_decompressors() as fresh:
        assert fresh.instances == []
        assert fresh.calls == fresh.largest_output == 0


def test_recorder_observes_both_instances_in_a_raw_deflate_retry() -> None:
    payload = b"x" * 100
    raw = b"\x00\x00\x00\xff\xff" + zlib.compress(payload, wbits=-zlib.MAX_WBITS)
    with record_decompressors() as recording:
        decoder = bounded_body._decompressobj(zlib.MAX_WBITS)
        with pytest.raises(zlib.error):
            decoder.decompress(raw, max_length=32)
        decoder = bounded_body._decompressobj(-zlib.MAX_WBITS)
        output = decoder.decompress(raw, max_length=32)
        while decoder.unconsumed_tail:
            output += decoder.decompress(decoder.unconsumed_tail, max_length=32)
        assert output == payload
        assert decoder.eof
        assert len(recording.instances) == 2
        assert [instance.calls for instance in recording.instances] == [1, 4]
        assert recording.calls == 5
        assert recording.largest_output == 32


def test_recorder_restores_the_factory_when_the_context_raises() -> None:
    original = bounded_body._decompressobj
    with pytest.raises(ValueError, match="test error"), record_decompressors():
        raise ValueError("test error")
    assert bounded_body._decompressobj is original


def test_recording_search_metrics_covers_the_protocol_and_is_instance_local() -> None:
    metrics = RecordingSearchMetrics()
    sink: SearchMetricsSink = metrics
    assert metrics.counters == {name: 0 for name in get_type_hints(SearchMetricsSink)}
    sink.fallback_fired += 1
    sink.paid_calls += 2
    sink.classification_wait_timeouts += 3
    assert metrics.counters == {
        "fallback_fired": 1,
        "paid_calls": 2,
        "classification_wait_timeouts": 3,
        "provider_compressed_body": 0,
        "provider_timeouts": 0,
        "promptguard_latency_target_exceeded": 0,
        "sanitization_latency_max_ms": 0,
    }
    assert all(type(value) is int for value in metrics.counters.values())
    assert all(value == 0 for value in RecordingSearchMetrics().counters.values())
