"""`/retrieve` admission and off-loop stages (`hardening-retrieve-parity` US-002).

Every test here owns its controller: the module-level
``app.state.retrieve_admission`` is one process-wide instance shared by every
lifespan-free test, and these tests saturate it on purpose. Each fixture below
swaps in a fresh one and asserts ``active``, ``queued`` and
``queued_bytes`` back at zero on the way out, so an off-by-one here cannot
present as an unrelated downstream timeout somewhere else.
"""

from __future__ import annotations

import asyncio
import gc
import threading
import weakref
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any, cast
from unittest.mock import MagicMock, patch

import httpx
import pytest

from cache import CacheMetrics
from model_fetcher import ModelMetrics
from models import RetrieveRequest
from pipeline import contract, orchestrator
from pipeline.extraction_limits import extraction_settings_from_config
from pipeline.orchestrator import PipelineError, run_retrieve_pipeline
from pipeline.retrieve_limits import retrieve_settings_from_config
from pipeline.stage1_extraction import ExtractionResult, extract_html
from pipeline.stage2_structural import scan_structural
from pipeline.stage4_structuring import structure_sanitization_result
from pipeline.stage5_url_audit import FetchResult
from promptguard.classifier import PromptGuardClassifier
from retrieval_app import (
    ExtractionAdmissionController,
    ExtractionMetrics,
    RetrieveMetrics,
    SearchMetrics,
    app,
)
from tests.fakes import FakeContentCache

_URL = "https://example.com/"
_PAGE = b"<html><body><p>A calm page about gardening.</p></body></html>"
_N_PLUS_ONE = 5


def _fetch_result(body: bytes = _PAGE) -> FetchResult:
    return FetchResult(
        final_url=_URL,
        response_body=body,
        content_type="text/html",
        status_code=200,
    )


def _loaded_classifier() -> MagicMock:
    classifier = MagicMock(spec=PromptGuardClassifier)
    classifier.loaded = True
    classifier.classify.return_value = (0.0, [])
    return classifier


def _assert_idle(controller: ExtractionAdmissionController) -> None:
    assert (controller.active, controller.queued, controller.queued_bytes) == (
        0,
        0,
        0,
    )


class _Admission:
    """The app wired for `/retrieve` under one test's own admission controller."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.settings = retrieve_settings_from_config(config)
        self.metrics = RetrieveMetrics()
        self.controller = ExtractionAdmissionController.from_retrieve_settings(
            self.settings, self.metrics
        )
        self.semaphore = asyncio.Semaphore(1)
        self.classifier = _loaded_classifier()

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        state = app.state
        values: dict[str, object] = {
            "cache": FakeContentCache(),
            "classifier": self.classifier,
            "config": {},
            "sanitizer_revision": "revision-under-test",
            "retrieve_settings": self.settings,
            "retrieve_metrics": self.metrics,
            "retrieve_admission": self.controller,
            "classification_semaphore": self.semaphore,
            "extraction_settings": extraction_settings_from_config({}),
            "extraction_metrics": ExtractionMetrics(),
            "search_metrics": SearchMetrics(),
            "cache_metrics": CacheMetrics(),
            "model_metrics": ModelMetrics(),
        }
        for name, value in values.items():
            monkeypatch.setattr(state, name, value, raising=False)

    async def run(self, **overrides: Any) -> Any:
        """Drive the pipeline directly under this controller."""
        return await run_retrieve_pipeline(
            RetrieveRequest(url=_URL),
            cache=None,
            classifier=overrides.pop("classifier", None),
            config={},
            sanitizer_revision="revision-under-test",
            promptguard_threshold=0.85,
            settings=self.settings,
            retrieve_metrics=self.metrics,
            classification_semaphore=self.semaphore,
            extraction_settings=extraction_settings_from_config({}),
            admission=self.controller,
        )


def _admission_fixture(name: str, config: dict[str, Any]) -> Callable[..., Any]:
    @pytest.fixture(name=name)
    async def _fixture(
        monkeypatch: pytest.MonkeyPatch,
    ) -> AsyncGenerator[_Admission, None]:
        admission = _Admission(config)
        admission.install(monkeypatch)
        monkeypatch.setattr(
            "pipeline.orchestrator.validate_url",
            _async_return(("93.184.216.34", "example.com")),
        )
        yield admission
        _assert_idle(admission.controller)

    return _fixture


def _async_return(value: Any) -> Callable[..., Awaitable[Any]]:
    async def _returns(*_args: Any, **_kwargs: Any) -> Any:
        return value

    return _returns


depth_one = _admission_fixture("depth_one", {"retrieve": {"admission_queue_depth": 1}})
byte_bound = _admission_fixture(
    "byte_bound",
    {"retrieve": {"admission_queue_depth": 4, "max_queued_fetch_bytes": 10485760}},
)
defaults = _admission_fixture("defaults", {})


class _GatedFetch:
    """A `fetch_url` that counts calls and parks each one until opened."""

    def __init__(self) -> None:
        self.calls = 0
        self.gate = asyncio.Event()
        self.entered = asyncio.Event()

    async def __call__(self, url: str, **_kwargs: Any) -> FetchResult:
        self.calls += 1
        self.entered.set()
        await self.gate.wait()
        return _fetch_result()


async def _until(predicate: Callable[[], bool]) -> None:
    async with asyncio.timeout(10):
        while not predicate():
            await asyncio.sleep(0.005)


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


# ---------------------------------------------------------------------------
# The bounds: a queued request holds nothing, a full queue refuses at once
# ---------------------------------------------------------------------------


async def test_depth_bound_queues_without_fetching_then_refuses_422(
    depth_one: _Admission, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One held, one queued (not fetched), the third refused 422 at once."""
    fetch = _GatedFetch()
    monkeypatch.setattr("pipeline.orchestrator.fetch_url", fetch)
    controller = depth_one.controller

    async with _client() as client:
        first = asyncio.create_task(client.post("/retrieve", json={"url": _URL}))
        await _until(lambda: controller.active == 1 and fetch.calls == 1)
        second = asyncio.create_task(client.post("/retrieve", json={"url": _URL}))
        await _until(lambda: controller.queued == 1)
        assert fetch.calls == 1

        third = await asyncio.wait_for(
            client.post("/retrieve", json={"url": _URL}), timeout=1.0
        )
        assert first.done() is False
        assert third.status_code == 422
        assert third.json()["error"] == "busy"
        assert third.json()["reason"] == contract.RETRIEVE_ADMISSION_QUEUE_FULL
        assert set(third.json()) == {"error", "reason", "request_id"}

        fetch.gate.set()
        assert (await first).status_code == 200
        assert (await second).status_code == 200
        assert fetch.calls == 2

        retrieve = (await client.get("/metrics")).json()["retrieve"]
    assert retrieve["semaphore_saturation"] == 2
    assert retrieve["busy_rejections"] == 1
    assert retrieve["errors"] == {"busy": 1}


async def test_byte_bound_refuses_the_second_queued_request(
    byte_bound: _Admission, monkeypatch: pytest.MonkeyPatch
) -> None:
    """At 10 MB of queued bytes one fetch-cap reservation fills the queue."""
    fetch = _GatedFetch()
    monkeypatch.setattr("pipeline.orchestrator.fetch_url", fetch)
    controller = byte_bound.controller

    holder = asyncio.create_task(byte_bound.run())
    await _until(lambda: controller.active == 1 and fetch.calls == 1)
    queued = asyncio.create_task(byte_bound.run())
    await _until(lambda: controller.queued == 1)
    assert controller.queued_bytes == 10485760

    with pytest.raises(PipelineError) as refused:
        await byte_bound.run()
    assert refused.value.error == "busy"
    assert refused.value.reason == contract.RETRIEVE_ADMISSION_QUEUE_FULL
    assert byte_bound.metrics.busy_rejections == 1

    fetch.gate.set()
    await holder
    await queued


# ---------------------------------------------------------------------------
# Permit discipline: every path hands the slot back, N+1 times over
# ---------------------------------------------------------------------------


async def _queue_full(admission: _Admission, fetch: _GatedFetch) -> None:
    holder = asyncio.create_task(admission.run())
    await _until(lambda: admission.controller.active == 1 and fetch.entered.is_set())
    queued = asyncio.create_task(admission.run())
    await _until(lambda: admission.controller.queued == 1)
    with pytest.raises(PipelineError) as refused:
        await admission.run()
    assert refused.value.error == "busy"
    fetch.gate.set()
    await holder
    await queued


async def _fetch_error(admission: _Admission, fetch: _GatedFetch) -> None:
    del fetch
    with (
        patch(
            "pipeline.orchestrator.fetch_url",
            new=_raises(RuntimeError("connection reset")),
        ),
        pytest.raises(PipelineError) as refused,
    ):
        await admission.run()
    assert refused.value.error == "fetch_error"


async def _extraction_exception(admission: _Admission, fetch: _GatedFetch) -> None:
    fetch.gate.set()

    def _explodes(*_args: object) -> object:
        raise ValueError("parser fell over")

    with (
        patch("pipeline.orchestrator.extract_html", new=_explodes),
        pytest.raises(ValueError, match="parser fell over"),
    ):
        await admission.run()


async def _cancelled_while_holding(admission: _Admission, fetch: _GatedFetch) -> None:
    holder = asyncio.create_task(admission.run())
    await _until(lambda: admission.controller.active == 1 and fetch.entered.is_set())
    holder.cancel()
    with pytest.raises(asyncio.CancelledError):
        await holder


async def _cancelled_while_queued(admission: _Admission, fetch: _GatedFetch) -> None:
    controller = admission.controller
    holder = asyncio.create_task(admission.run())
    await _until(lambda: controller.active == 1 and fetch.entered.is_set())
    queued = asyncio.create_task(admission.run())
    await _until(lambda: controller.queued == 1)
    queued.cancel()
    # Pinned ordering: the cancelled waiter runs its `except BaseException`
    # and leaves `_waiters` *before* the holder releases. The other
    # interleaving — a release popping the already-cancelled future first —
    # is the recorded handoff residual (GOTCHAS.md), not a tested property.
    with pytest.raises(asyncio.CancelledError):
        await queued
    assert (controller.active, controller.queued, controller.queued_bytes) == (
        1,
        0,
        0,
    )
    fetch.gate.set()
    await holder


def _raises(exc: BaseException) -> Callable[..., Awaitable[Any]]:
    async def _raise(*_args: Any, **_kwargs: Any) -> Any:
        raise exc

    return _raise


_PATHS: dict[str, Callable[[_Admission, _GatedFetch], Awaitable[None]]] = {
    "queue_full": _queue_full,
    "fetch_error": _fetch_error,
    "extraction_exception": _extraction_exception,
    "cancelled_while_holding": _cancelled_while_holding,
    "cancelled_while_queued": _cancelled_while_queued,
}


@pytest.mark.parametrize("path", sorted(_PATHS))
async def test_every_path_hands_the_slot_back(
    depth_one: _Admission, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    """Five times each against one fetch slot, then the next request fetches."""
    controller = depth_one.controller
    for _ in range(_N_PLUS_ONE):
        fetch = _GatedFetch()
        monkeypatch.setattr("pipeline.orchestrator.fetch_url", fetch)
        await _PATHS[path](depth_one, fetch)
        _assert_idle(controller)

        after = _GatedFetch()
        after.gate.set()
        monkeypatch.setattr("pipeline.orchestrator.fetch_url", after)
        await depth_one.run()
        assert after.calls == 1
        _assert_idle(controller)


@pytest.mark.parametrize("cancel_count", [1, 3])
@pytest.mark.parametrize("worker_fails", [False, True])
async def test_html_cancellation_retains_slot_until_extractor_exits(
    depth_one: _Admission,
    monkeypatch: pytest.MonkeyPatch,
    cancel_count: int,
    worker_fails: bool,
) -> None:
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    finish = threading.Event()
    exited = threading.Event()
    calls = 0
    active = 0
    peak = 0
    lock = threading.Lock()

    def extract(text: str, url: str) -> ExtractionResult:
        nonlocal active, peak, calls
        with lock:
            calls += 1
            first = calls == 1
            active += 1
            peak = max(peak, active)
        try:
            if first:
                loop.call_soon_threadsafe(entered.set)
                assert finish.wait(5), "test did not release HTML worker"
                if worker_fails:
                    raise ValueError("extraction-failure-sentinel")
            return extract_html(text, url)
        finally:
            with lock:
                active -= 1
            if first:
                exited.set()

    fetch = _GatedFetch()
    fetch.gate.set()
    monkeypatch.setattr(orchestrator, "fetch_url", fetch)
    monkeypatch.setattr(orchestrator, "extract_html", extract)
    controller = depth_one.controller
    first = asyncio.create_task(depth_one.run())
    second: asyncio.Task[Any] | None = None
    try:
        await asyncio.wait_for(entered.wait(), 5)
        for _ in range(cancel_count):
            first.cancel()
            await asyncio.sleep(0)
            assert not first.done()
            assert not exited.is_set()
            assert controller.active == 1
        second = asyncio.create_task(depth_one.run())
        await _until(lambda: controller.queued == 1)
        assert fetch.calls == 1
        assert calls == 1
        assert controller.queued_bytes == 10485760
        with pytest.raises(PipelineError) as refused:
            await depth_one.run()
        assert refused.value.error == "busy"
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert exited.is_set()
        await second
        assert fetch.calls == 2
        assert calls == 2
        assert peak == 1
        assert active == 0
        _assert_idle(controller)
    finally:
        finish.set()
        first.cancel()
        if second is not None:
            second.cancel()
        await asyncio.gather(
            first, *([second] if second is not None else []), return_exceptions=True
        )


@pytest.mark.parametrize("redirects", [False, True])
async def test_total_fetch_deadline_stops_periodic_stream_and_unblocks_queue(
    depth_one: _Admission,
    monkeypatch: pytest.MonkeyPatch,
    redirects: bool,
) -> None:
    """Real fetcher, fake transport: chunks cannot renew the absolute deadline."""
    started = asyncio.Event()
    closed = asyncio.Event()
    chunks: list[float] = []
    requests: list[str] = []
    loop = asyncio.get_running_loop()
    deadline = 0.2

    class SlowStream(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncGenerator[bytes]:
            started.set()
            # Every chunk is far inside the inactivity interval. Without the
            # total deadline this stream keeps the sole slot indefinitely.
            while True:
                chunks.append(loop.time())
                yield b"small chunk "
                await asyncio.sleep(0.01)

        async def aclose(self) -> None:
            closed.set()

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path == "/":
            if redirects:
                await asyncio.sleep(0.08)
                return httpx.Response(302, headers={"location": "/redirected"})
            return httpx.Response(200, stream=SlowStream())
        if request.url.path == "/redirected":
            await asyncio.sleep(0.04)
            return httpx.Response(200, stream=SlowStream())
        return httpx.Response(200, content=_PAGE, headers={"content-type": "text/html"})

    # Only transport and DNS are faked; use the production fetcher and its
    # redirect/body handling without sockets or relaxed pytest-socket guards.
    real_client = httpx.AsyncClient

    def mock_client(**kwargs: Any) -> httpx.AsyncClient:
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr("pipeline.stage5_url_audit.httpx.AsyncClient", mock_client)
    monkeypatch.setattr(
        "pipeline.stage5_url_audit.validate_url",
        _async_return(("93.184.216.34", "example.com")),
    )
    monkeypatch.setattr(orchestrator, "DEFAULT_TIMEOUT", deadline)

    # The queued request's deadline begins only after admission.
    async def unrelated() -> object:
        return await run_retrieve_pipeline(
            RetrieveRequest(url="https://example.com/unrelated"),
            cache=None,
            classifier=None,
            config={},
            sanitizer_revision="test",
            promptguard_threshold=0.85,
            settings=depth_one.settings,
            retrieve_metrics=depth_one.metrics,
            classification_semaphore=depth_one.semaphore,
            extraction_settings=extraction_settings_from_config({}),
            admission=depth_one.controller,
        )

    start = loop.time()
    first = asyncio.create_task(depth_one.run())
    tasks = [first]
    try:
        await asyncio.wait_for(started.wait(), 5)
        second = asyncio.create_task(unrelated())
        tasks.append(second)
        await _until(lambda: depth_one.controller.queued == 1)
        assert "/unrelated" not in requests
        with pytest.raises(PipelineError) as expired:
            await asyncio.wait_for(first, 2)
        assert expired.value.error == "fetch_timeout"
        assert expired.value.reason == f"Request timed out fetching {_URL}"
        assert closed.is_set()
        assert len(chunks) >= 2
        assert loop.time() - start < 0.6
        await asyncio.wait_for(second, 2)
        assert requests == (
            ["/", "/redirected", "/unrelated"] if redirects else ["/", "/unrelated"]
        )
        _assert_idle(depth_one.controller)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


# ---------------------------------------------------------------------------
# Stages 1, 2 and 4 run off the event loop
# ---------------------------------------------------------------------------


async def test_stages_one_two_and_four_run_off_the_event_loop(
    defaults: _Admission, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each threaded call records a thread that is not the loop's."""
    loop_thread = threading.get_ident()
    seen: dict[str, int] = {}

    def _recording(name: str, real: Callable[..., Any]) -> Callable[..., Any]:
        def _call(*args: Any, **kwargs: Any) -> Any:
            seen[name] = threading.get_ident()
            return real(*args, **kwargs)

        return _call

    fetch = _GatedFetch()
    fetch.gate.set()
    monkeypatch.setattr("pipeline.orchestrator.fetch_url", fetch)
    for name, real in (
        ("extract_html", extract_html),
        ("scan_structural", scan_structural),
        ("structure_sanitization_result", structure_sanitization_result),
    ):
        monkeypatch.setattr(f"pipeline.orchestrator.{name}", _recording(name, real))

    await defaults.run()
    assert set(seen) == {
        "extract_html",
        "scan_structural",
        "structure_sanitization_result",
    }
    assert loop_thread not in set(seen.values())

    # `/extract` shares stages 2 and 4 through `sanitize_and_structure`.
    seen.clear()
    await orchestrator.run_extract_pipeline(
        b"plain uploaded text",
        filename="notes.txt",
        mime_hint="text/plain",
        extract_mode="full",
        request_id="req-1",
        classifier=None,
        promptguard_threshold=0.85,
        sanitizer_revision="revision-under-test",
    )
    assert set(seen) == {"scan_structural", "structure_sanitization_result"}
    assert loop_thread not in set(seen.values())


# ---------------------------------------------------------------------------
# The fetched body goes with the slot
# ---------------------------------------------------------------------------


class _Body(bytearray):
    """A weak-referenceable stand-in for the fetched body (``bytes`` is not)."""


async def test_the_body_is_dead_while_the_request_waits_on_the_permit(
    defaults: _Admission, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Parked on a held classification permit, the request holds no body."""
    refs: list[weakref.ref[_Body]] = []

    async def _fetch(url: str, **_kwargs: Any) -> FetchResult:
        body = _Body(_PAGE)
        refs.append(weakref.ref(body))
        return _fetch_result(cast("bytes", body))

    monkeypatch.setattr("pipeline.orchestrator.fetch_url", _fetch)
    semaphore = defaults.semaphore
    await semaphore.acquire()
    request = asyncio.create_task(defaults.run(classifier=defaults.classifier))
    try:
        try:
            await _until(lambda: bool(semaphore._waiters))
            assert defaults.controller.active == 0
            gc.collect()
            assert len(refs) == 1
            assert refs[0]() is None
            assert request.done() is False
        finally:
            semaphore.release()
        content = await asyncio.wait_for(request, 5)
        assert content.promptguard_state == "scanned"
    finally:
        request.cancel()
        await asyncio.gather(request, return_exceptions=True)
