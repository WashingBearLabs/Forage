"""Stage 1 -- PDF text extraction.

Extracts text from the PDF text layer using ``pypdf``.  No OCR is performed.

All PDF metadata (Author, Title, Subject, Keywords, Creator, Producer) is
deliberately stripped -- none of it propagates into the returned
``ExtractionResult``.

Security notes
--------------
* ``pypdf`` does **not** execute embedded JavaScript.  We never call
  ``PdfReader.attachments`` or any method that would evaluate /JS or /AA
  actions, so JavaScript payloads inside the PDF are inert.
* Metadata is discarded unconditionally -- it cannot be used for injection.
"""

from __future__ import annotations

import io

from pypdf import PdfReader

from pipeline.stage1_extraction import ExtractionResult, normalize_text

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Magic bytes at the start of every valid PDF file
_PDF_MAGIC = b"%PDF-"

# Maximum PDF file size (50 MB)
_MAX_PDF_SIZE = 50 * 1024 * 1024

# Page separator used when concatenating text from multiple pages
_PAGE_SEPARATOR = "\n\n"


# ---------------------------------------------------------------------------
# Content-type detection
# ---------------------------------------------------------------------------


def detect_content_type(
    content_type_header: str | None,
    content_bytes: bytes,
) -> str:
    """Return ``'pdf'`` or ``'html'`` based on header and magic bytes.

    Parameters
    ----------
    content_type_header:
        The ``Content-Type`` response header value (may be ``None``).
    content_bytes:
        The raw response body (at least the first 5 bytes are inspected).

    Returns
    -------
    ``'pdf'`` when the content is a PDF document, ``'html'`` otherwise.
    """
    # 1. Check Content-Type header
    if content_type_header:
        ct_lower = content_type_header.lower()
        if "application/pdf" in ct_lower:
            return "pdf"

    # 2. Check magic bytes
    if content_bytes[:5] == _PDF_MAGIC:
        return "pdf"

    return "html"


# ---------------------------------------------------------------------------
# PDF extraction
# ---------------------------------------------------------------------------


class PDFExtractionError(Exception):
    """Raised when PDF text extraction fails."""


class PDFTooLargeError(PDFExtractionError):
    """Raised when a PDF exceeds the extraction byte limit."""


class PDFEncryptedError(PDFExtractionError):
    """Raised when a PDF is encrypted, including with an empty password."""


class PDFNoTextError(PDFExtractionError):
    """Raised when a PDF has no extractable text layer."""


def extract_pdf(pdf_bytes: bytes) -> ExtractionResult:
    """Extract text from a PDF's text layer.

    Parameters
    ----------
    pdf_bytes:
        Raw bytes of the PDF file.

    Returns
    -------
    ``ExtractionResult`` with ``title``, ``author``, and ``date`` set to
    ``None`` (metadata is intentionally stripped).

    Raises
    ------
    PDFExtractionError
        If the PDF contains no extractable text (e.g. scanned/image PDF).
    """
    if len(pdf_bytes) > _MAX_PDF_SIZE:
        raise PDFTooLargeError(
            f"PDF exceeds maximum size of {_MAX_PDF_SIZE // (1024 * 1024)}MB"
        )

    reader = PdfReader(io.BytesIO(pdf_bytes))
    if reader.is_encrypted:
        # Test whether a blank password opens the document, but never extract
        # from encrypted PDFs: their classification must not depend on pypdf.
        reader.decrypt("")
        raise PDFEncryptedError("PDF is encrypted")

    # -- Extract text from every page --
    page_texts: list[str] = []
    for page in reader.pages:
        text = page.extract_text() or ""
        page_texts.append(text)

    raw_text = _PAGE_SEPARATOR.join(page_texts)

    # -- Check for image-only PDFs (no text layer) --
    if not raw_text.strip():
        raise PDFNoTextError(
            "PDF contains no extractable text (scanned/image PDF). "
            "OCR is not supported."
        )

    # -- Normalize (same pipeline as HTML extraction) --
    raw_text = normalize_text(raw_text)

    # For PDFs there is no boilerplate distinction, so main_content == raw_text
    main_content = raw_text
    word_count = len(main_content.split()) if main_content else 0

    # Metadata is intentionally stripped -- title, author, date are all None.
    # pypdf exposes reader.metadata but we never read or forward it.
    return ExtractionResult(
        title=None,
        author=None,
        date=None,
        raw_text=raw_text,
        main_content=main_content,
        word_count=word_count,
    )
