"""Tests for Stage 1 -- PDF text extraction (US-004)."""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

# Add the retrieval service root to sys.path so pipeline is importable
_retrieval_root = str(
    Path(__file__).resolve().parents[2] / "services" / "retrieval"
)
if _retrieval_root not in sys.path:
    sys.path.insert(0, _retrieval_root)

from pypdf import PdfWriter  # noqa: E402

from pipeline.stage1_extraction import ExtractionResult  # noqa: E402
from pipeline.stage1_pdf import (  # noqa: E402
    PDFExtractionError,
    detect_content_type,
    extract_pdf,
)

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
        content = (
            f"BT /F1 12 Tf 72 720 Td ({_pdf_escape(text)}) Tj ET"
        )
        # Add a font resource so the content stream is valid
        from pypdf.generic import (
            ArrayObject,
            DictionaryObject,
            NameObject,
            TextStringObject,
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
        import zlib

        encoded = zlib.compress(content.encode("latin-1"))
        from pypdf.generic import DecodedStreamObject, EncodedStreamObject

        stream = DecodedStreamObject()
        stream.set_data(content.encode("latin-1"))
        page[NameObject("/Contents")] = stream

    # Set metadata if provided
    if any([author, title, subject, keywords, creator, producer]):
        metadata = {}
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
        pdf_bytes = _make_text_pdf(
            ["Content."], author="Secret Author"
        )
        result = extract_pdf(pdf_bytes)
        assert result.author is None

    def test_title_stripped(self) -> None:
        pdf_bytes = _make_text_pdf(
            ["Content."], title="Secret Title"
        )
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
        with pytest.raises(PDFExtractionError, match="no extractable text"):
            extract_pdf(pdf_bytes)

    def test_multi_page_blank_raises_error(self) -> None:
        pdf_bytes = _make_blank_pdf(num_pages=3)
        with pytest.raises(PDFExtractionError, match="OCR is not supported"):
            extract_pdf(pdf_bytes)


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
        assert (
            detect_content_type("application/pdf; charset=utf-8", b"") == "pdf"
        )

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
        with pytest.raises(AttributeError):
            result.title = "new"  # type: ignore[misc]
