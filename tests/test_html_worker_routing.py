"""Large ``/retrieve`` HTML bodies use the worker (``release-resource-bounds`` US-006).

Three layers: the routing decision and failure mapping against a stubbed worker
seam, real-task cancellation with the worker held (admission is released only
after the worker is reaped and its spool unlinked), and the default-versus-``0``
comparison — every corpus page record through the in-process frame round trip,
and a fixed sample through real spawns.

PYTEST_DONT_REWRITE: assertion rewriting is off for this module, so a failing
assert shows only its message, never its operands (which could carry record
text). ``tests/test_corpus_lint.py`` requires this marker in every corpus test
module.
"""

from __future__ import annotations

import asyncio
import errno
import json
import logging
import os
import stat
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from models import RetrieveRequest
from pipeline import contract, html_subprocess, orchestrator, worker_launch
from pipeline.extraction_limits import ExtractionSettings
from pipeline.html_subprocess import (
    MAX_HTML_FRAME_BYTES,
    HTMLExtractionError,
    build_html_frame,
    decode_html_frame,
    encode_html_input,
)
from pipeline.retrieve_limits import (
    RETRIEVE_HTML_WORKER_THRESHOLD_BYTES,
    RetrieveConfigurationError,
    RetrieveSettings,
    retrieve_settings_from_config,
)
from pipeline.stage1_extraction import ExtractionResult
from pipeline.stage2_structural import StructuralScanResult
from pipeline.stage5_url_audit import FetchResult
from retrieval_app import ExtractionAdmissionController, RetrieveMetrics
from scripts.corpus.records import load_corpus, page_document
from tests.fakes import make_mock_classifier

_URL = "https://example.com/page"
_THRESHOLD = 1000
_SAMPLE_SIZE = 12
_CORPUS_CHUNK_BUDGETS = (0, 64)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _body(length: int) -> bytes:
    prefix = b"<html><body><p>"
    suffix = b"</p></body></html>"
    return prefix + b"a" * (length - len(prefix) - len(suffix)) + suffix


def _extraction() -> ExtractionResult:
    return ExtractionResult(
        title="T",
        author=None,
        date=None,
        raw_text="text",
        main_content="text",
        word_count=1,
        main_content_is_fallback=False,
    )


@pytest.fixture
def spool_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    spool = tmp_path / "spool"
    spool.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(spool))
    return spool


def _leftovers(root: Path) -> list[Path]:
    directory = root / f"forage-spool-{os.geteuid()}"
    return list(directory.iterdir()) if directory.exists() else []


class _Harness:
    """One ``run_retrieve_pipeline`` call under its own admission controller."""

    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        body: bytes,
        threshold: int = _THRESHOLD,
        max_promptguard_chunks: int = 0,
    ) -> None:
        self.settings = RetrieveSettings(
            html_worker_threshold_bytes=threshold,
            max_promptguard_chunks=max_promptguard_chunks,
            admission_queue_depth=0,
        )
        self.metrics = RetrieveMetrics()
        self.admission = ExtractionAdmissionController.from_retrieve_settings(
            self.settings, self.metrics
        )
        self.classifier: MagicMock = make_mock_classifier()
        self.fetch = AsyncMock(
            return_value=FetchResult(
                response_body=body,
                content_type="text/html",
                final_url=_URL,
                redirect_chain=[],
                domain_changed_on_redirect=False,
            )
        )
        monkeypatch.setattr(orchestrator, "validate_url", AsyncMock())
        monkeypatch.setattr(orchestrator, "fetch_url", self.fetch)

    async def run(self, **overrides: Any) -> Any:
        return await orchestrator.run_retrieve_pipeline(
            RetrieveRequest(url=_URL),
            cache=None,
            classifier=self.classifier,
            config={},
            sanitizer_revision="a" * 64,
            promptguard_threshold=0.85,
            settings=self.settings,
            retrieve_metrics=self.metrics,
            classification_semaphore=asyncio.Semaphore(1),
            extraction_settings=ExtractionSettings(),
            admission=self.admission,
            **overrides,
        )

    def assert_idle(self) -> None:
        a = self.admission
        assert (a.active, a.queued, a.queued_bytes) == (0, 0, 0)


class _Seams:
    """Records which stage-1 seam ran, returning a canned extraction."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.in_thread = 0
        self.worker = 0
        self.worker_args: tuple[Any, ...] = ()
        monkeypatch.setattr(orchestrator, "extract_html_and_scan", self._in_thread)
        monkeypatch.setattr(
            orchestrator, "extract_html_bytes_in_subprocess", self._worker
        )

    def _in_thread(self, *args: Any) -> tuple[ExtractionResult, None]:
        del args
        self.in_thread += 1
        return _extraction(), None

    def _worker(self, *args: Any) -> tuple[ExtractionResult, None]:
        self.worker += 1
        self.worker_args = args
        return _extraction(), None


# ---------------------------------------------------------------------------
# The routing decision
# ---------------------------------------------------------------------------


class TestThresholdBoundary:
    async def test_a_body_exactly_at_the_threshold_parses_in_thread(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        harness = _Harness(monkeypatch, body=_body(_THRESHOLD))
        seams = _Seams(monkeypatch)

        await harness.run()

        assert (seams.in_thread, seams.worker) == (1, 0)
        assert harness.metrics.html_worker_spawns == 0
        harness.assert_idle()

    async def test_one_byte_over_the_threshold_goes_to_the_worker(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        harness = _Harness(monkeypatch, body=_body(_THRESHOLD + 1))
        seams = _Seams(monkeypatch)

        await harness.run()

        assert (seams.in_thread, seams.worker) == (0, 1)
        body, url, budget, settings = seams.worker_args
        assert len(body) == _THRESHOLD + 1
        assert (url, budget) == (_URL, None)
        assert isinstance(settings, ExtractionSettings)
        assert harness.metrics.html_worker_spawns == 1
        assert harness.metrics.html_worker_refusals == 0
        harness.assert_idle()

    async def test_zero_sends_every_body_to_the_worker(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        harness = _Harness(monkeypatch, body=_body(100), threshold=0)
        seams = _Seams(monkeypatch)

        await harness.run()

        assert (seams.in_thread, seams.worker) == (0, 1)
        harness.assert_idle()

    async def test_the_comparison_is_on_bytes_not_decoded_characters(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # 600 two-byte characters: 600 characters, 1200 bytes.
        body = ("<p>" + "é" * 600 + "</p>").encode()
        assert len(body) > _THRESHOLD > len(body.decode())
        harness = _Harness(monkeypatch, body=body)
        seams = _Seams(monkeypatch)

        await harness.run()

        assert (seams.in_thread, seams.worker) == (0, 1)

    async def test_the_worker_receives_the_character_budget(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        harness = _Harness(
            monkeypatch, body=_body(_THRESHOLD + 1), max_promptguard_chunks=64
        )
        seams = _Seams(monkeypatch)

        await harness.run()

        assert seams.worker_args[2] == harness.settings.max_extracted_characters
        assert seams.worker_args[2] is not None


# ---------------------------------------------------------------------------
# Failure mapping
# ---------------------------------------------------------------------------


_SENTINEL = "exception-text-sentinel"


class TestFailureMapping:
    @staticmethod
    def _raising(exc: Exception) -> Callable[..., object]:
        def _worker(*args: Any) -> object:
            del args
            raise exc

        return _worker

    async def test_a_worker_failure_is_a_coded_422_and_a_refusal(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        harness = _Harness(monkeypatch, body=_body(_THRESHOLD + 1))
        monkeypatch.setattr(
            orchestrator,
            "extract_html_bytes_in_subprocess",
            self._raising(HTMLExtractionError(_SENTINEL)),
        )
        caplog.set_level(logging.WARNING)

        with pytest.raises(orchestrator.PipelineError) as refused:
            await harness.run()

        assert refused.value.error == "extraction_failed"
        assert refused.value.reason == contract.RETRIEVE_HTML_EXTRACTION_ERROR
        assert refused.value.reason == "html_extraction_error"
        assert _SENTINEL not in str(refused.value.reason)
        assert harness.metrics.html_worker_spawns == 1
        assert harness.metrics.html_worker_refusals == 1
        harness.classifier.classify_windows.assert_not_called()
        harness.assert_idle()
        warnings = [
            record.getMessage()
            for record in caplog.records
            if record.name == "pipeline.orchestrator"
            and record.levelno >= logging.WARNING
        ]
        assert warnings == ["retrieve_html_extraction_failed"]

    @pytest.mark.parametrize(
        "exc",
        [
            OSError(errno.ENOSPC, _SENTINEL),
            worker_launch.SpoolDirectoryError(_SENTINEL),
        ],
        ids=["oserror", "spool_directory_error"],
    )
    async def test_a_spool_fault_maps_to_the_existing_spool_reason(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        exc: Exception,
    ) -> None:
        harness = _Harness(monkeypatch, body=_body(_THRESHOLD + 1))
        monkeypatch.setattr(
            orchestrator, "extract_html_bytes_in_subprocess", self._raising(exc)
        )
        caplog.set_level(logging.WARNING)

        with pytest.raises(orchestrator.PipelineError) as refused:
            await harness.run()

        assert refused.value.error == "extraction_failed"
        assert refused.value.reason == contract.RETRIEVE_PDF_SPOOL_ERROR
        assert _SENTINEL not in str(refused.value.reason)
        # A host fault is not the worker refusing the page.
        assert harness.metrics.html_worker_spawns == 1
        assert harness.metrics.html_worker_refusals == 0
        harness.assert_idle()
        assert [
            record.getMessage()
            for record in caplog.records
            if record.name == "pipeline.orchestrator"
            and record.levelno >= logging.WARNING
        ] == ["retrieve_spool_error"]

    async def test_the_in_thread_path_counts_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        harness = _Harness(monkeypatch, body=_body(_THRESHOLD))
        _Seams(monkeypatch)

        await harness.run()

        assert (
            harness.metrics.html_worker_spawns,
            harness.metrics.html_worker_refusals,
        ) == (0, 0)


# ---------------------------------------------------------------------------
# Cancellation: admission is held until the worker is reaped and unlinked
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cancel_count", [1, 3])
@pytest.mark.parametrize("fails", [False, True], ids=["success", "worker_failure"])
async def test_task_cancellation_keeps_admission_until_reap_and_unlink(
    spool_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    cancel_count: int,
    fails: bool,
) -> None:
    """A cancelled await cannot detach the thread, spool or admission owner."""
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    release = threading.Event()
    reaped = threading.Event()
    spooled: list[Path] = []

    def held_worker(
        kind: str, argv: list[str], **kwargs: Any
    ) -> dict[str, object] | None:
        del kind, kwargs
        spooled.append(Path(argv[0]))
        loop.call_soon_threadsafe(started.set)
        try:
            assert release.wait(5), "test did not release the HTML worker"
            if fails:
                return None
            return build_html_frame("<html><body><p>x</p></body></html>", _URL, None)
        finally:
            reaped.set()

    monkeypatch.setattr(html_subprocess, "run_worker", held_worker)
    harness = _Harness(monkeypatch, body=_body(_THRESHOLD + 1))

    task = asyncio.create_task(harness.run())
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        assert harness.admission.active == 1
        assert len(spooled) == 1
        assert stat.S_IMODE(spooled[0].stat().st_mode) == 0o600
        assert spooled[0].name.startswith("forage-retrieve-html-")
        for _ in range(cancel_count):
            assert task.cancel("request cancelled")
            await asyncio.sleep(0)
            assert not task.done(), "cancellation escaped before the worker was reaped"
            assert not reaped.is_set()
            assert spooled[0].exists()
            assert harness.admission.active == 1
            with pytest.raises(orchestrator.PipelineError) as refused:
                await harness.run()
            assert refused.value.error == "busy"
        harness.fetch.assert_awaited_once()
    finally:
        release.set()
        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), timeout=5)
        assert await asyncio.to_thread(reaped.wait, 5)

    assert task.cancelled()
    assert not spooled[0].exists()
    assert _leftovers(spool_root) == []
    harness.assert_idle()
    assert await harness.admission.acquire()
    await harness.admission.release()
    harness.classifier.classify_windows.assert_not_called()


# ---------------------------------------------------------------------------
# The knob
# ---------------------------------------------------------------------------


class TestThresholdKnob:
    def test_the_default_is_the_calibrated_512_kib(self) -> None:
        assert RETRIEVE_HTML_WORKER_THRESHOLD_BYTES == 512 * 1024
        settings = retrieve_settings_from_config({})
        assert settings.html_worker_threshold_bytes == 524288

    @pytest.mark.parametrize("value", [0, 1, 524288, 2 * 524288])
    def test_values_from_zero_to_twice_the_default_are_accepted(
        self, value: int
    ) -> None:
        settings = retrieve_settings_from_config(
            {"retrieve": {"html_worker_threshold_bytes": value}}
        )
        assert settings.html_worker_threshold_bytes == value

    @pytest.mark.parametrize("value", [-1, 2 * 524288 + 1, 10**9, True, "512", 1.5])
    def test_anything_else_refuses_boot(self, value: object) -> None:
        with pytest.raises(RetrieveConfigurationError):
            retrieve_settings_from_config(
                {"retrieve": {"html_worker_threshold_bytes": value}}
            )


# ---------------------------------------------------------------------------
# Default versus 0: the served response does not depend on the path
# ---------------------------------------------------------------------------


def _round_trip_worker(
    body: bytes, url: str | None, budget: int | None, settings: ExtractionSettings
) -> tuple[ExtractionResult, StructuralScanResult | None]:
    """The worker seam, run in-process: encode, child half, wire, decode."""
    del settings
    html = body.decode("utf-8", errors="replace")
    # The child disables logging before it parses.
    logging.disable(logging.CRITICAL)
    try:
        frame = build_html_frame(html, url, budget)
    finally:
        logging.disable(logging.NOTSET)
    framed = worker_launch.encode_frame(
        frame, ensure_ascii=False, max_bytes=MAX_HTML_FRAME_BYTES
    )
    return decode_html_frame(json.loads(framed[4:]))


def _page_bodies() -> list[tuple[str, bytes]]:
    return [
        (record.id, page_document(record).encode("utf-8"))
        for record in load_corpus()
        if record.surface == "page"
    ]


async def _served(
    monkeypatch: pytest.MonkeyPatch, body: bytes, *, threshold: int, chunks: int
) -> dict[str, Any]:
    harness = _Harness(
        monkeypatch, body=body, threshold=threshold, max_promptguard_chunks=chunks
    )
    try:
        served = await harness.run()
    except orchestrator.PipelineError as refused:
        return {"error": refused.error, "reason": refused.reason}
    # The two fields that are per-call by construction: a uuid and a clock read.
    return {
        **served.model_dump(mode="json"),
        "request_id": None,
        "retrieved_at": None,
    }


@pytest.mark.parametrize("chunks", _CORPUS_CHUNK_BUDGETS)
async def test_every_corpus_page_serves_the_same_response_on_either_path(
    monkeypatch: pytest.MonkeyPatch, chunks: int
) -> None:
    monkeypatch.setattr(
        orchestrator, "extract_html_bytes_in_subprocess", _round_trip_worker
    )
    bodies = _page_bodies()
    assert len(bodies) > 300
    ceiling = max(len(body) for _, body in bodies)
    mismatched: list[str] = []
    for record_id, body in bodies:
        in_thread = await _served(monkeypatch, body, threshold=ceiling, chunks=chunks)
        worker = await _served(monkeypatch, body, threshold=0, chunks=chunks)
        if in_thread != worker:
            mismatched.append(record_id)
    assert mismatched == []


async def test_real_spawns_serve_the_same_response_on_a_corpus_sample(
    spool_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bodies = _page_bodies()
    step = len(bodies) // _SAMPLE_SIZE
    sample = bodies[::step][:_SAMPLE_SIZE]
    assert len(sample) == _SAMPLE_SIZE
    ceiling = max(len(body) for _, body in bodies)
    mismatched: list[str] = []
    for record_id, body in sample:
        in_thread = await _served(monkeypatch, body, threshold=ceiling, chunks=64)
        worker = await _served(monkeypatch, body, threshold=0, chunks=64)
        if in_thread != worker:
            mismatched.append(record_id)
    assert mismatched == []
    assert _leftovers(spool_root) == []


def test_the_spool_header_carries_the_url_not_argv() -> None:
    # The worker seam is handed the URL for the header, never as an argument.
    assert _URL.encode() in encode_html_input(b"<p>x</p>", _URL)
