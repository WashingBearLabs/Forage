"""`/search` parses titles and snippets off the event-loop thread.

Structural properties only: thread identity, a ticker that progresses while a
stub parse does a GIL-releasing ``time.sleep``, cancellation ownership, and the
latency counters under a stub delay. The GIL still serialises CPU work, so none
of this proves a hostile parse is fast -- only that the loop thread is not the
one running it.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable
from typing import Any
from unittest.mock import patch

import pytest

from models import SearchRequest, Stage3Verdict
from pipeline import orchestrator
from pipeline.search_providers.base import ProviderSearchResult
from pipeline.stage1_extraction import ExtractionResult
from pipeline.stage2_structural import StructuralScanResult
from pipeline.stage3_promptguard import PromptGuardResult
from tests.fakes import FakeSearchProvider, RecordingSearchMetrics

_SAFE = PromptGuardResult(verdict=Stage3Verdict.SAFE, score=0.1)


def _provider(count: int) -> FakeSearchProvider:
    return FakeSearchProvider(
        outcome=ProviderSearchResult(
            provider_name="fake",
            results=[
                {
                    "title": f"Title {index}",
                    "url": f"https://example.com/{index}",
                    "content": f"Snippet {index}",
                    "engine": "fake",
                }
                for index in range(count)
            ],
            unresponsive_engines=[],
        )
    )


async def _run(count: int, **kwargs: Any) -> Any:
    return await orchestrator.run_search_pipeline(
        SearchRequest(query="synthetic search", num_results=count),
        providers=[_provider(count)],
        config={},
        **kwargs,
    )


def _wrapping(
    real: Callable[..., Any], sink: list[int], delay: float = 0.0
) -> Callable[..., Any]:
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        sink.append(threading.get_ident())
        if delay:
            time.sleep(delay)
        return real(*args, **kwargs)

    return wrapper


async def test_parse_and_markup_scans_never_run_on_the_loop_thread() -> None:
    loop_thread = threading.get_ident()
    parses: list[int] = []
    markups: list[int] = []
    with (
        patch.object(
            orchestrator,
            "extract_html",
            _wrapping(orchestrator.extract_html, parses),
        ),
        patch.object(
            orchestrator,
            "scan_raw_markup",
            _wrapping(orchestrator.scan_raw_markup, markups),
        ),
        patch("pipeline.orchestrator.run_promptguard", return_value=_SAFE),
    ):
        response = await _run(4)
    assert len(response.results) == 4
    # Two fields per result, two markup scans per result.
    assert len(parses) == 8
    assert len(markups) == 8
    assert loop_thread not in parses
    assert loop_thread not in markups


async def test_loop_ticker_progresses_during_a_sleeping_parse() -> None:
    ticks = 0
    stop = asyncio.Event()

    async def ticker() -> None:
        nonlocal ticks
        while not stop.is_set():
            ticks += 1
            await asyncio.sleep(0.005)

    parses: list[int] = []
    with (
        patch.object(
            orchestrator,
            "extract_html",
            _wrapping(orchestrator.extract_html, parses, delay=0.05),
        ),
        patch("pipeline.orchestrator.run_promptguard", return_value=_SAFE),
    ):
        task = asyncio.create_task(ticker())
        await asyncio.sleep(0)
        await _run(3)
        stop.set()
        await task
    # 6 parses x 50 ms of sleep: a loop-blocking parse would allow ~1 tick.
    assert ticks >= 10


async def test_zero_results_make_no_thread_hop() -> None:
    parses: list[int] = []
    with patch.object(
        orchestrator,
        "extract_html",
        _wrapping(orchestrator.extract_html, parses),
    ):
        provider = _provider(0)
        response = await orchestrator.run_search_pipeline(
            SearchRequest(query="synthetic search", num_results=3),
            providers=[provider],
            config={},
        )
    assert response.results == []
    assert parses == []


async def test_cancelled_search_waits_for_its_parse_thread() -> None:
    started = threading.Event()
    finished = threading.Event()

    real = orchestrator.extract_html

    def blocking(*args: Any, **kwargs: Any) -> ExtractionResult:
        started.set()
        time.sleep(0.3)
        try:
            return real(*args, **kwargs)
        finally:
            finished.set()

    with (
        patch.object(orchestrator, "extract_html", blocking),
        patch("pipeline.orchestrator.run_promptguard", return_value=_SAFE),
    ):
        task = asyncio.create_task(_run(2))
        while not started.is_set():
            await asyncio.sleep(0.005)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        # The task only finishes cancelling once the thread is done.
        assert finished.is_set()


async def test_latency_counters_include_the_hop_and_no_wait_is_charged() -> None:
    metrics = RecordingSearchMetrics()
    delay = 0.04
    parses: list[int] = []
    with (
        patch.object(
            orchestrator,
            "extract_html",
            _wrapping(orchestrator.extract_html, parses, delay=delay),
        ),
        patch("pipeline.orchestrator.run_promptguard", return_value=_SAFE),
    ):
        await _run(
            3,
            search_metrics=metrics,
            classification_semaphore=asyncio.Semaphore(1),
            classification_wait_seconds=30.0,
        )
    stub_ms = int(len(parses) * delay * 1000)
    assert metrics.classification_wait_timeouts == 0
    assert metrics.promptguard_latency_target_exceeded == 0
    # The same whole-loop floor the on-loop parse produced, plus a small hop.
    assert stub_ms <= metrics.sanitization_latency_max_ms < stub_ms + 400


def test_scan_result_fields_is_pure_and_total() -> None:
    scans = orchestrator._scan_search_result_fields({"title": 5, "content": None})
    assert scans.title == ("", "", "")
    assert isinstance(scans.title_markup, StructuralScanResult)
