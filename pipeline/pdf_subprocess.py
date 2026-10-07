"""Killable, spawn-isolated PDF parsing for untrusted uploads and fetched PDFs."""

from __future__ import annotations

from pathlib import Path

from pipeline.extraction_limits import ExtractionSettings
from pipeline.stage1_extraction import ExtractionResult, normalize_text
from pipeline.stage1_pdf import (
    PDFEncryptedError,
    PDFExtractionError,
    PDFNoTextError,
)
from pipeline.worker_launch import SpoolDirectoryError as SpoolDirectoryError
from pipeline.worker_launch import run_worker, spooled_bytes
from pipeline.worker_launch import spool_dir as spool_dir

# Spool-file name prefix for fetched PDFs; the spool helpers live in worker_launch.
_SPOOL_PREFIX = "forage-retrieve-"


class PDFClassifiableTextLimitError(PDFExtractionError):
    """Raised when extracted PDF text exceeds the PromptGuard budget."""


class PDFPageLimitError(PDFExtractionError):
    """Raised when a PDF exceeds the fixed page-count extraction bound."""


def _extract_pdf_path(path: Path, settings: ExtractionSettings) -> ExtractionResult:
    """Extract one PDF from a sidecar-owned file without loading input bytes first."""
    from pypdf import PdfReader

    with path.open("rb") as source:
        reader = PdfReader(source)
        if reader.is_encrypted:
            reader.decrypt("")
            raise PDFEncryptedError("PDF is encrypted")
        if len(reader.pages) > settings.max_pages:
            raise PDFPageLimitError("PDF exceeds the page extraction limit")

        page_texts: list[str] = []
        extracted_characters = 0
        extracted_bytes = 0
        for page in reader.pages:
            page_text = page.extract_text() or ""
            extracted_characters += len(page_text)
            extracted_bytes += len(page_text.encode("utf-8"))
            if (
                extracted_characters > settings.max_extracted_characters
                or extracted_bytes > settings.max_extracted_output_bytes
            ):
                raise PDFClassifiableTextLimitError(
                    "PDF text exceeds the PromptGuard classification budget"
                )
            page_texts.append(page_text)

    raw_text = normalize_text("\n\n".join(page_texts))
    if (
        len(raw_text) > settings.max_extracted_characters
        or len(raw_text.encode("utf-8")) > settings.max_extracted_output_bytes
    ):
        raise PDFClassifiableTextLimitError(
            "PDF text exceeds the PromptGuard classification budget"
        )
    if not raw_text:
        raise PDFNoTextError(
            "PDF contains no extractable text (scanned/image PDF). "
            "OCR is not supported."
        )
    return ExtractionResult(
        title=None,
        author=None,
        date=None,
        raw_text=raw_text,
        main_content=raw_text,
        word_count=len(raw_text.split()),
    )


def run_pdf_worker(path: Path, settings: ExtractionSettings) -> dict[str, object]:
    """Child side: parse one spooled PDF into a capped, closed-vocabulary payload."""
    try:
        result = _extract_pdf_path(path, settings)
    except PDFClassifiableTextLimitError:
        return {"status": "too_large_to_classify"}
    except PDFEncryptedError:
        return {"status": "encrypted"}
    except PDFNoTextError:
        return {"status": "no_text"}
    return {
        "status": "ok",
        "raw_text": result.raw_text,
        "word_count": result.word_count,
    }


def extract_pdf_in_subprocess(
    path: Path,
    settings: ExtractionSettings,
) -> ExtractionResult:
    """Run pypdf in a launched worker and kill/reap it on every abnormal outcome."""
    payload = run_worker(
        "pdf",
        [str(path), str(settings.max_pages), str(settings.max_promptguard_chunks)],
        cpu_seconds=settings.child_cpu_seconds,
        address_space_bytes=settings.child_address_space_bytes,
        wall_clock_seconds=settings.wall_clock_seconds,
        max_frame_bytes=settings.max_ipc_result_bytes,
    )

    if payload is None or payload.get("status") == "failed":
        raise PDFExtractionError("PDF extraction worker failed")
    if payload.get("status") == "encrypted":
        raise PDFEncryptedError("PDF is encrypted")
    if payload.get("status") == "no_text":
        raise PDFNoTextError("PDF contains no extractable text")
    if payload.get("status") == "too_large_to_classify":
        raise PDFClassifiableTextLimitError(
            "PDF text exceeds the PromptGuard classification budget"
        )
    if payload.get("status") != "ok":
        raise PDFExtractionError("PDF extraction worker returned an invalid result")

    raw_text = payload.get("raw_text")
    word_count = payload.get("word_count")
    if (
        not isinstance(raw_text, str)
        or not isinstance(word_count, int)
        or len(raw_text) > settings.max_extracted_characters
        or len(raw_text.encode("utf-8")) > settings.max_extracted_output_bytes
    ):
        raise PDFExtractionError("PDF extraction worker returned an invalid result")
    return ExtractionResult(
        title=None,
        author=None,
        date=None,
        raw_text=raw_text,
        main_content=raw_text,
        word_count=word_count,
    )


def extract_pdf_bytes_in_subprocess(
    data: bytes,
    settings: ExtractionSettings,
) -> ExtractionResult:
    """Spool fetched PDF bytes and parse them in the same bounded worker.

    ``/retrieve`` already holds the body in memory, so the spool buys reuse of
    ``extract_pdf_in_subprocess``'s entry point and rlimit wiring, not memory
    isolation. The file is created in :func:`spool_dir`, made 0600 before a
    byte is written, and unlinked in ``finally`` on success or any raised
    failure. Cancelling an async caller does not interrupt this synchronous
    function: the caller must retain ownership of its thread until it returns,
    including cleanup, before propagating cancellation or releasing admission.
    An ``OSError`` from the directory check, the create or the write propagates
    unchanged for the caller to map; an already-created file is still unlinked.
    """
    with spooled_bytes(data, prefix=_SPOOL_PREFIX) as path:
        return extract_pdf_in_subprocess(path, settings)
