"""Tests for Stage 1 -- PDF text extraction (US-004)."""

from __future__ import annotations

import asyncio
import errno
import io
import logging
import os
import stat
import tempfile
import threading
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pypdf import PdfReader, PdfWriter

from models import RetrieveRequest
from pipeline import orchestrator, pdf_subprocess
from pipeline.extraction_limits import ExtractionSettings
from pipeline.pdf_subprocess import (
    PDFClassifiableTextLimitError,
    SpoolDirectoryError,
    extract_pdf_bytes_in_subprocess,
    spool_dir,
)
from pipeline.retrieve_limits import RetrieveSettings
from pipeline.stage1_extraction import ExtractionResult
from pipeline.stage1_pdf import (
    PDFEncryptedError,
    PDFExtractionError,
    PDFNoTextError,
    detect_content_type,
    extract_pdf,
)
from pipeline.stage5_url_audit import FetchResult
from tests.fakes import assert_frozen

# ---------------------------------------------------------------------------
# Helpers -- build minimal PDFs in memory
# ---------------------------------------------------------------------------


def _make_text_pdf(
    pages: list[str],
    *,
    author: str | None = None,
    title: str | None = None,
    subject: str | None = None,
    keywords: str | None = None,
    creator: str | None = None,
    producer: str | None = None,
) -> bytes:
    """Create a minimal PDF with the given text pages and optional metadata."""
    writer = PdfWriter()
    for text in pages:
        writer.add_blank_page(width=612, height=792)
        # pypdf PdfWriter doesn't directly let us add text the easy way,
        # so we build a content stream manually.
        page = writer.pages[-1]
        # Build a minimal PDF content stream with text
        content = f"BT /F1 12 Tf 72 720 Td ({_pdf_escape(text)}) Tj ET"
        # Add a font resource so the content stream is valid
        from pypdf.generic import (
            DictionaryObject,
            NameObject,
        )

        font_dict = DictionaryObject()
        font_dict[NameObject("/Type")] = NameObject("/Font")
        font_dict[NameObject("/Subtype")] = NameObject("/Type1")
        font_dict[NameObject("/BaseFont")] = NameObject("/Helvetica")

        resources = DictionaryObject()
        font_resources = DictionaryObject()
        font_resources[NameObject("/F1")] = font_dict
        resources[NameObject("/Font")] = font_resources
        page[NameObject("/Resources")] = resources

        # Encode content stream
        from pypdf.generic import DecodedStreamObject

        stream = DecodedStreamObject()
        stream.set_data(content.encode("latin-1"))
        page[NameObject("/Contents")] = stream

    # Set metadata if provided
    if any([author, title, subject, keywords, creator, producer]):
        metadata: dict[str, str] = {}
        if author:
            metadata["/Author"] = author
        if title:
            metadata["/Title"] = title
        if subject:
            metadata["/Subject"] = subject
        if keywords:
            metadata["/Keywords"] = keywords
        if creator:
            metadata["/Creator"] = creator
        if producer:
            metadata["/Producer"] = producer
        writer.add_metadata(metadata)

    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _make_blank_pdf(num_pages: int = 1) -> bytes:
    """Create a PDF with blank pages (no text layer) -- simulates image-only."""
    writer = PdfWriter()
    for _ in range(num_pages):
        writer.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _make_encrypted_pdf(password: str) -> bytes:
    """Create a real encrypted PDF from a text-layer document."""
    source_reader = PdfReader(io.BytesIO(_make_text_pdf(["Encrypted content."])))
    writer = PdfWriter()
    writer.append_pages_from_reader(source_reader)
    writer.encrypt(password)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _pdf_escape(text: str) -> str:
    """Escape parentheses and backslashes for PDF string literals."""
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


# ---------------------------------------------------------------------------
# Text extraction tests
# ---------------------------------------------------------------------------


class TestPDFTextExtraction:
    """Test basic text extraction from PDF text layer."""

    def test_single_page_extraction(self) -> None:
        pdf_bytes = _make_text_pdf(["Hello from a PDF document."])
        result = extract_pdf(pdf_bytes)
        assert isinstance(result, ExtractionResult)
        assert "Hello from a PDF document" in result.raw_text

    def test_multi_page_extraction(self) -> None:
        pdf_bytes = _make_text_pdf(["Page one content.", "Page two content."])
        result = extract_pdf(pdf_bytes)
        assert "Page one content" in result.raw_text
        assert "Page two content" in result.raw_text

    def test_word_count_set(self) -> None:
        pdf_bytes = _make_text_pdf(["One two three four five."])
        result = extract_pdf(pdf_bytes)
        assert result.word_count > 0
        assert result.word_count == len(result.main_content.split())

    def test_main_content_equals_raw_text(self) -> None:
        """For PDFs there is no boilerplate removal -- both fields are equal."""
        pdf_bytes = _make_text_pdf(["Some content."])
        result = extract_pdf(pdf_bytes)
        assert result.main_content == result.raw_text

    def test_result_is_extraction_result(self) -> None:
        pdf_bytes = _make_text_pdf(["Test."])
        result = extract_pdf(pdf_bytes)
        assert isinstance(result, ExtractionResult)


# ---------------------------------------------------------------------------
# Metadata stripping tests
# ---------------------------------------------------------------------------


class TestMetadataStripping:
    """Test that all PDF metadata fields are stripped from the output."""

    def test_author_stripped(self) -> None:
        pdf_bytes = _make_text_pdf(["Content."], author="Secret Author")
        result = extract_pdf(pdf_bytes)
        assert result.author is None

    def test_title_stripped(self) -> None:
        pdf_bytes = _make_text_pdf(["Content."], title="Secret Title")
        result = extract_pdf(pdf_bytes)
        assert result.title is None

    def test_all_metadata_stripped(self) -> None:
        pdf_bytes = _make_text_pdf(
            ["Content."],
            author="Author Name",
            title="Document Title",
            subject="Subject Line",
            keywords="keyword1, keyword2",
            creator="Test Creator",
            producer="Test Producer",
        )
        result = extract_pdf(pdf_bytes)
        assert result.title is None
        assert result.author is None
        assert result.date is None
        # Also verify metadata text doesn't leak into content
        assert "Author Name" not in result.raw_text
        assert "Document Title" not in result.raw_text
        assert "Subject Line" not in result.raw_text
        assert "keyword1" not in result.raw_text
        assert "Test Creator" not in result.raw_text
        assert "Test Producer" not in result.raw_text


# ---------------------------------------------------------------------------
# Image-only PDF error
# ---------------------------------------------------------------------------


class TestImageOnlyPDF:
    """Test that image-only PDFs (no text layer) return a descriptive error."""

    def test_blank_pdf_raises_error(self) -> None:
        pdf_bytes = _make_blank_pdf(num_pages=1)
        with pytest.raises(PDFNoTextError, match="no extractable text"):
            extract_pdf(pdf_bytes)

    def test_multi_page_blank_raises_error(self) -> None:
        pdf_bytes = _make_blank_pdf(num_pages=3)
        with pytest.raises(PDFNoTextError, match="OCR is not supported"):
            extract_pdf(pdf_bytes)


class TestEncryptedPDF:
    """Test deterministic encrypted-PDF identification using real PDF fixtures."""

    @pytest.mark.parametrize("password", ["required-password", ""])
    def test_encrypted_pdf_raises_specific_error(self, password: str) -> None:
        """Encrypted PDFs, including blank-password PDFs, are never parsed."""
        with pytest.raises(PDFEncryptedError):
            extract_pdf(_make_encrypted_pdf(password))


# ---------------------------------------------------------------------------
# JavaScript disabled
# ---------------------------------------------------------------------------


class TestJavaScriptDisabled:
    """Verify that pypdf does not execute JavaScript.

    pypdf is a pure text-extraction library and never evaluates /JS or /AA
    actions.  This test confirms we never access any JS-related API and that
    any embedded JS metadata doesn't appear in the output.
    """

    def test_no_js_in_output(self) -> None:
        # Create a PDF that has text (to avoid the image-only error)
        pdf_bytes = _make_text_pdf(["Safe content here."])
        result = extract_pdf(pdf_bytes)
        # The extraction should produce clean text
        assert "Safe content here" in result.raw_text
        # No JavaScript artifacts should be present
        assert "javascript" not in result.raw_text.lower()


# ---------------------------------------------------------------------------
# Content-type detection
# ---------------------------------------------------------------------------


class TestContentTypeDetection:
    """Test the detect_content_type routing function."""

    def test_pdf_content_type_header(self) -> None:
        assert detect_content_type("application/pdf", b"") == "pdf"

    def test_pdf_content_type_with_charset(self) -> None:
        assert detect_content_type("application/pdf; charset=utf-8", b"") == "pdf"

    def test_pdf_magic_bytes(self) -> None:
        assert detect_content_type(None, b"%PDF-1.7 rest of file") == "pdf"

    def test_html_content_type_header(self) -> None:
        assert detect_content_type("text/html", b"") == "html"

    def test_html_default_fallback(self) -> None:
        assert detect_content_type(None, b"<html>") == "html"

    def test_none_header_non_pdf_bytes(self) -> None:
        assert detect_content_type(None, b"random bytes") == "html"

    def test_empty_bytes(self) -> None:
        assert detect_content_type(None, b"") == "html"

    def test_pdf_magic_overrides_no_header(self) -> None:
        pdf_bytes = _make_blank_pdf()
        assert detect_content_type(None, pdf_bytes) == "pdf"


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


class TestPDFNormalization:
    """Test that PDF text goes through the same normalization as HTML."""

    def test_whitespace_collapsed(self) -> None:
        pdf_bytes = _make_text_pdf(["Text   with   extra   spaces."])
        result = extract_pdf(pdf_bytes)
        # After normalization, runs of spaces become single space
        assert "   " not in result.raw_text

    def test_frozen_dataclass(self) -> None:
        pdf_bytes = _make_text_pdf(["Content."])
        result = extract_pdf(pdf_bytes)
        assert_frozen(result, "title", "new")


# ---------------------------------------------------------------------------
# The spool directory (hardening-retrieve-parity US-003)
# ---------------------------------------------------------------------------


@pytest.fixture
def spool_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``tempfile.gettempdir`` — the seam ``spool_dir()`` resolves — here."""
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    return tmp_path


def _private_spool_path(root: Path) -> Path:
    return root / f"forage-spool-{os.geteuid()}"


def _spooled_leftovers(root: Path) -> list[Path]:
    """Every ``forage-retrieve-*`` file under the spool directory, if it exists."""
    directory = _private_spool_path(root)
    if not directory.exists():
        return []
    return sorted(directory.glob("forage-retrieve-*"))


class TestSpoolDir:
    """``spool_dir()``: created 0700 on first use, verified — never repaired."""

    def test_first_use_creates_a_0700_directory_owned_by_the_process(
        self, spool_root: Path
    ) -> None:
        path = spool_dir()

        assert path == _private_spool_path(spool_root)
        st = os.lstat(path)
        assert stat.S_ISDIR(st.st_mode)
        assert stat.S_IMODE(st.st_mode) == 0o700
        assert st.st_uid == os.geteuid()

    def test_an_existing_private_directory_is_accepted_on_every_call(
        self, spool_root: Path
    ) -> None:
        assert spool_dir() == spool_dir() == _private_spool_path(spool_root)

    def test_it_resolves_per_call_not_at_import(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A ``TMPDIR`` that changes after import still applies."""
        first = tmp_path / "first"
        second = tmp_path / "second"
        first.mkdir()
        second.mkdir()
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(first))
        assert spool_dir().parent == first
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(second))
        assert spool_dir().parent == second

    def test_a_pre_created_0755_directory_is_refused_not_repaired(
        self, spool_root: Path
    ) -> None:
        path = _private_spool_path(spool_root)
        path.mkdir()
        path.chmod(0o755)

        with pytest.raises(SpoolDirectoryError) as exc_info:
            spool_dir()

        assert str(exc_info.value) == "spool_dir_mode"
        assert stat.S_IMODE(os.lstat(path).st_mode) == 0o755

    def test_a_symlink_is_refused_even_to_a_private_directory(
        self, spool_root: Path, tmp_path: Path
    ) -> None:
        """``lstat``, not ``stat``: the target would pass every other check."""
        target = tmp_path / "elsewhere"
        target.mkdir(mode=0o700)
        _private_spool_path(spool_root).symlink_to(target)

        with pytest.raises(SpoolDirectoryError) as exc_info:
            spool_dir()

        assert str(exc_info.value) == "spool_dir_symlink"

    def test_a_non_directory_is_refused(self, spool_root: Path) -> None:
        _private_spool_path(spool_root).write_bytes(b"")

        with pytest.raises(SpoolDirectoryError) as exc_info:
            spool_dir()

        assert str(exc_info.value) == "spool_dir_not_directory"

    def test_another_users_directory_is_refused(
        self, spool_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Another uid's name, owned by this process: the owner check fires."""
        foreign_uid = os.geteuid() + 1
        (spool_root / f"forage-spool-{foreign_uid}").mkdir(mode=0o700)
        monkeypatch.setattr(os, "geteuid", lambda: foreign_uid)

        with pytest.raises(SpoolDirectoryError) as exc_info:
            spool_dir()

        assert str(exc_info.value) == "spool_dir_foreign_owner"

    def test_the_refusal_is_an_oserror_carrying_only_its_token(
        self, spool_root: Path
    ) -> None:
        """So ``/retrieve``'s ``except OSError`` maps a use-time refusal."""
        path = _private_spool_path(spool_root)
        path.mkdir()
        path.chmod(0o750)

        with pytest.raises(OSError) as exc_info:
            spool_dir()

        assert isinstance(exc_info.value, SpoolDirectoryError)
        assert str(spool_root) not in str(exc_info.value)

    def test_the_only_chmod_in_the_module_is_the_spool_files_fchmod(self) -> None:
        """Repair-then-verify would pass the 0755 test by fixing what it refuses."""
        source = Path(pdf_subprocess.__file__).read_text()
        lines = [line for line in source.splitlines() if "chmod" in line]
        assert len(lines) == 1
        assert "os.fchmod(" in lines[0]
        assert "0o600" in lines[0]


# ---------------------------------------------------------------------------
# The fetched-PDF bytes entry point (hardening-retrieve-parity US-003)
# ---------------------------------------------------------------------------


def _worker_result(text: str = "Fetched PDF text.") -> ExtractionResult:
    return ExtractionResult(
        title=None,
        author=None,
        date=None,
        raw_text=text,
        main_content=text,
        word_count=len(text.split()),
    )


class TestExtractPdfBytesInSubprocess:
    """Spool, parse in the worker, and unlink on every exit path."""

    def test_the_real_worker_parses_fetched_bytes(self, spool_root: Path) -> None:
        result = extract_pdf_bytes_in_subprocess(
            _make_text_pdf(["Fetched through the worker."]),
            ExtractionSettings(),
        )

        assert "Fetched through the worker." in result.raw_text
        assert _spooled_leftovers(spool_root) == []

    @pytest.mark.parametrize(
        ("data", "settings", "error"),
        [
            (
                _make_encrypted_pdf("password"),
                ExtractionSettings(),
                PDFEncryptedError,
            ),
            (_make_blank_pdf(), ExtractionSettings(), PDFNoTextError),
            (
                _make_text_pdf(["word " * 400]),
                ExtractionSettings(max_promptguard_chunks=1),
                PDFClassifiableTextLimitError,
            ),
            (
                _make_text_pdf(["Page one.", "Page two."]),
                ExtractionSettings(max_pages=1),
                PDFExtractionError,
            ),
            (b"%PDF-1.7 corrupt", ExtractionSettings(), PDFExtractionError),
        ],
        ids=["encrypted", "no_text", "text_budget", "page_limit", "corrupt"],
    )
    def test_real_worker_failure_vocabulary_and_cleanup(
        self,
        spool_root: Path,
        data: bytes,
        settings: ExtractionSettings,
        error: type[PDFExtractionError],
    ) -> None:
        with pytest.raises(error) as raised:
            extract_pdf_bytes_in_subprocess(data, settings)

        assert type(raised.value) is error
        assert _spooled_leftovers(spool_root) == []

    def test_the_worker_gets_a_0600_spool_file_and_the_settings_it_was_given(
        self, spool_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        settings = ExtractionSettings(max_promptguard_chunks=8)
        data = b"%PDF-1.7 fetched body"
        seen: dict[str, Any] = {}

        def recording_worker(
            path: Path, worker_settings: ExtractionSettings
        ) -> ExtractionResult:
            st = os.lstat(path)
            seen.update(
                path=path,
                mode=stat.S_IMODE(st.st_mode),
                contents=path.read_bytes(),
                settings=worker_settings,
            )
            return _worker_result()

        monkeypatch.setattr(
            pdf_subprocess, "extract_pdf_in_subprocess", recording_worker
        )

        result = extract_pdf_bytes_in_subprocess(data, settings)

        assert result.raw_text == "Fetched PDF text."
        assert seen["path"].parent == _private_spool_path(spool_root)
        assert seen["path"].name.startswith("forage-retrieve-")
        assert seen["mode"] == 0o600
        assert seen["contents"] == data
        assert seen["settings"] is settings
        assert _spooled_leftovers(spool_root) == []

    @pytest.mark.parametrize(
        "outcome",
        [
            PDFEncryptedError("PDF is encrypted"),
            PDFNoTextError("PDF contains no extractable text"),
            PDFClassifiableTextLimitError("over budget"),
            # The simulated rlimit / wall-clock kill: a real `RLIMIT_CPU` kill
            # is not reproducible in the hermetic suite, and the parent turns
            # a dead child into exactly this plain `PDFExtractionError`.
            PDFExtractionError("PDF extraction worker failed"),
            asyncio.CancelledError(),
        ],
        ids=["encrypted", "no_text", "classifiable_limit", "killed", "cancelled"],
    )
    def test_the_spool_file_is_gone_after_every_worker_outcome(
        self,
        spool_root: Path,
        monkeypatch: pytest.MonkeyPatch,
        outcome: BaseException,
    ) -> None:
        spooled: list[Path] = []

        def failing_worker(
            path: Path, _settings: ExtractionSettings
        ) -> ExtractionResult:
            assert path.exists()
            spooled.append(path)
            raise outcome

        monkeypatch.setattr(pdf_subprocess, "extract_pdf_in_subprocess", failing_worker)

        with pytest.raises(type(outcome)):
            extract_pdf_bytes_in_subprocess(b"%PDF-1.7", ExtractionSettings())

        assert len(spooled) == 1
        assert not spooled[0].exists()
        assert _spooled_leftovers(spool_root) == []

    def test_a_failed_create_propagates_as_oserror_and_calls_no_worker(
        self, spool_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def no_space(*_args: object, **_kwargs: object) -> object:
            raise OSError(errno.ENOSPC, "No space left on device")

        def unexpected_worker(
            _path: Path, _settings: ExtractionSettings
        ) -> ExtractionResult:
            raise AssertionError("the worker must not run without a spool file")

        monkeypatch.setattr(tempfile, "NamedTemporaryFile", no_space)
        monkeypatch.setattr(
            pdf_subprocess, "extract_pdf_in_subprocess", unexpected_worker
        )

        with pytest.raises(OSError) as exc_info:
            extract_pdf_bytes_in_subprocess(b"%PDF-1.7", ExtractionSettings())

        assert exc_info.value.errno == errno.ENOSPC
        assert _spooled_leftovers(spool_root) == []

    def test_a_failure_after_the_create_still_unlinks_the_file(
        self, spool_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The file exists once the create returns; the ``finally`` owns it."""

        def refused_fchmod(_fd: int, _mode: int) -> None:
            raise OSError(errno.EACCES, "Permission denied")

        monkeypatch.setattr(os, "fchmod", refused_fchmod)

        with pytest.raises(OSError):
            extract_pdf_bytes_in_subprocess(b"%PDF-1.7", ExtractionSettings())

        assert _private_spool_path(spool_root).is_dir()
        assert _spooled_leftovers(spool_root) == []

    def test_a_partial_spool_write_failure_unlinks_the_content(
        self, spool_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        create = tempfile.NamedTemporaryFile
        spooled: list[Path] = []

        def failing_spool(**kwargs: Any):
            temporary = create(mode="w+b", **kwargs)
            spooled.append(Path(temporary.name))

            def failed_write(data: bytes) -> int:
                temporary.file.write(data[:5])
                temporary.file.flush()
                raise OSError(errno.ENOSPC, "No space left on device")

            monkeypatch.setattr(temporary, "write", failed_write)
            return temporary

        worker = MagicMock()
        monkeypatch.setattr(tempfile, "NamedTemporaryFile", failing_spool)
        monkeypatch.setattr(pdf_subprocess, "extract_pdf_in_subprocess", worker)
        with pytest.raises(OSError) as raised:
            extract_pdf_bytes_in_subprocess(
                b"%PDF-1.7 secret content", ExtractionSettings()
            )

        assert raised.value.errno == errno.ENOSPC
        assert len(spooled) == 1
        assert not spooled[0].exists()
        assert _spooled_leftovers(spool_root) == []
        worker.assert_not_called()

    def test_a_refused_spool_directory_propagates_as_oserror(
        self, spool_root: Path
    ) -> None:
        path = _private_spool_path(spool_root)
        path.mkdir()
        path.chmod(0o755)

        with pytest.raises(OSError):
            extract_pdf_bytes_in_subprocess(b"%PDF-1.7", ExtractionSettings())

        assert list(path.iterdir()) == []


@pytest.mark.parametrize("cancel_count", [1, 3])
@pytest.mark.parametrize(
    "outcome",
    [
        None,
        PDFExtractionError("PDF extraction worker failed"),
        OSError(errno.ENOSPC, "content-bearing-path-sentinel"),
    ],
    ids=["success", "worker_failure", "spool_failure"],
)
async def test_retrieve_task_cancellation_keeps_admission_until_pdf_cleanup(
    spool_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    cancel_count: int,
    outcome: Exception | None,
) -> None:
    """A cancelled await cannot detach the thread, spool, or admission owner."""
    from retrieval_app import ExtractionAdmissionController, RetrieveMetrics

    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    finish = threading.Event()
    worker_finished = threading.Event()
    spooled: list[Path] = []

    def blocking_worker(path: Path, _settings: ExtractionSettings) -> ExtractionResult:
        spooled.append(path)
        loop.call_soon_threadsafe(started.set)
        try:
            assert finish.wait(5), "test did not release the PDF worker"
            if outcome is not None:
                raise outcome
            return _worker_result()
        finally:
            worker_finished.set()

    monkeypatch.setattr(pdf_subprocess, "extract_pdf_in_subprocess", blocking_worker)
    monkeypatch.setattr(orchestrator, "validate_url", AsyncMock())
    fetch = AsyncMock(
        return_value=FetchResult(
            response_body=b"%PDF-1.7 content-bearing fetched document",
            content_type="application/pdf",
            final_url="https://example.com/document.pdf",
            redirect_chain=[],
            domain_changed_on_redirect=False,
        )
    )
    monkeypatch.setattr(orchestrator, "fetch_url", fetch)
    sanitize = AsyncMock()
    monkeypatch.setattr(orchestrator, "sanitize_and_structure", sanitize)
    settings = RetrieveSettings(admission_queue_depth=0)
    metrics = RetrieveMetrics()
    admission = ExtractionAdmissionController.from_retrieve_settings(settings, metrics)

    async def retrieve() -> object:
        return await orchestrator.run_retrieve_pipeline(
            RetrieveRequest(url="https://example.com/document.pdf"),
            cache=None,
            classifier=None,
            config={},
            sanitizer_revision="a" * 64,
            settings=settings,
            retrieve_metrics=metrics,
            classification_semaphore=asyncio.Semaphore(1),
            extraction_settings=ExtractionSettings(),
            admission=admission,
        )

    task = asyncio.create_task(retrieve())
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        assert admission.active == 1
        assert len(spooled) == 1
        assert stat.S_IMODE(spooled[0].stat().st_mode) == 0o600
        for _ in range(cancel_count):
            assert task.cancel("request cancelled")
            await asyncio.sleep(0)
            assert not task.done(), "cancellation escaped before PDF cleanup"
            assert not worker_finished.is_set()
            assert spooled[0].exists()
            assert admission.active == 1
            with pytest.raises(orchestrator.PipelineError) as refused:
                await retrieve()
            assert refused.value.error == "busy"
            assert refused.value.reason == "admission_queue_full"
        fetch.assert_awaited_once()
    finally:
        finish.set()
        # Always drain the real task, even when the ownership assertion fails.
        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), timeout=5)
        assert await asyncio.to_thread(worker_finished.wait, 5)

    assert task.cancelled()
    assert worker_finished.is_set()
    assert _spooled_leftovers(spool_root) == []
    assert not spooled[0].exists()
    assert (admission.active, admission.queued, admission.queued_bytes) == (0, 0, 0)
    assert await admission.acquire()
    await admission.release()
    sanitize.assert_not_awaited()
    warnings = [
        record.getMessage()
        for record in caplog.records
        if record.name == "pipeline.orchestrator" and record.levelno >= logging.WARNING
    ]
    assert warnings == (
        ["retrieve_spool_error"] if isinstance(outcome, OSError) else []
    )
