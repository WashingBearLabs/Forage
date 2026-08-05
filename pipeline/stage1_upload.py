"""Stage 1 extraction and validity checks for uploaded document bytes.

``mime_hint`` is advisory only: magic bytes and strict UTF-8 decoding decide
the format, so a caller cannot label arbitrary binary data as text.
"""

from __future__ import annotations

import codecs
import unicodedata
from pathlib import Path

from pipeline.extraction_limits import MAX_EXTRACTED_OUTPUT_BYTES
from pipeline.stage1_extraction import ExtractionResult, normalize_text

_PDF_MAGIC = b"%PDF-"
_MAX_CONTROL_CHARACTER_RATIO = 0.05


class UnsupportedUploadFormatError(ValueError):
    """Raised when uploaded bytes are neither a valid PDF nor valid text."""


class UploadTextClassifiableLimitError(ValueError):
    """Raised when valid text exceeds the available PromptGuard scan budget."""


def extract_upload_text(
    content_bytes: bytes, mime_hint: str | None = None
) -> ExtractionResult:
    """Strictly decode and normalize a valid UTF-8 upload as plain text.

    The optional ``mime_hint`` is deliberately not used to override decoding:
    it is caller-controlled metadata, not format evidence. Text with NUL
    bytes, no visible content, or over 5% non-whitespace control characters is
    rejected before normalization.
    """
    del mime_hint
    if not content_bytes:
        raise UnsupportedUploadFormatError("Upload is empty")

    try:
        text = content_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise UnsupportedUploadFormatError("Upload is not valid UTF-8 text") from exc

    if "\x00" in text:
        raise UnsupportedUploadFormatError("Upload contains NUL bytes")

    if not text.lstrip("\ufeff").strip():
        raise UnsupportedUploadFormatError("Upload contains no visible text")

    control_count = sum(
        1
        for character in text
        if unicodedata.category(character) == "Cc"
        and character not in {"\t", "\n", "\r"}
    )
    if control_count / len(text) > _MAX_CONTROL_CHARACTER_RATIO:
        raise UnsupportedUploadFormatError(
            "Upload contains too many control characters for text"
        )

    normalized = normalize_text(text)
    if not normalized:
        raise UnsupportedUploadFormatError("Upload contains no visible text")

    return ExtractionResult(
        title=None,
        author=None,
        date=None,
        raw_text=normalized,
        main_content=normalized,
        word_count=len(normalized.split()),
    )


def extract_upload_text_file(
    path: Path,
    *,
    max_characters: int,
    max_output_bytes: int = MAX_EXTRACTED_OUTPUT_BYTES,
    chunk_size: int = 64 * 1024,
) -> ExtractionResult:
    """Strictly decode a bounded text file without materializing a 50 MB upload."""
    decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
    parts: list[str] = []
    character_count = 0
    output_bytes = 0
    control_count = 0

    try:
        with path.open("rb") as source:
            while chunk := source.read(chunk_size):
                decoded = decoder.decode(chunk)
                character_count += len(decoded)
                output_bytes += len(decoded.encode("utf-8"))
                if character_count > max_characters:
                    raise UploadTextClassifiableLimitError(
                        "Upload text exceeds the PromptGuard classification budget"
                    )
                if output_bytes > max_output_bytes:
                    raise UploadTextClassifiableLimitError(
                        "Upload text exceeds the extracted output budget"
                    )
                parts.append(decoded)
                control_count += sum(
                    1
                    for character in decoded
                    if unicodedata.category(character) == "Cc"
                    and character not in {"\t", "\n", "\r"}
                )
            decoded = decoder.decode(b"", final=True)
    except UnicodeDecodeError as exc:
        raise UnsupportedUploadFormatError("Upload is not valid UTF-8 text") from exc

    character_count += len(decoded)
    output_bytes += len(decoded.encode("utf-8"))
    if character_count > max_characters:
        raise UploadTextClassifiableLimitError(
            "Upload text exceeds the PromptGuard classification budget"
        )
    if output_bytes > max_output_bytes:
        raise UploadTextClassifiableLimitError(
            "Upload text exceeds the extracted output budget"
        )
    parts.append(decoded)
    text = "".join(parts)
    if not text:
        raise UnsupportedUploadFormatError("Upload is empty")
    if "\x00" in text:
        raise UnsupportedUploadFormatError("Upload contains NUL bytes")
    if not text.lstrip("\ufeff").strip():
        raise UnsupportedUploadFormatError("Upload contains no visible text")
    if control_count / len(text) > _MAX_CONTROL_CHARACTER_RATIO:
        raise UnsupportedUploadFormatError(
            "Upload contains too many control characters for text"
        )

    normalized = normalize_text(text)
    if not normalized:
        raise UnsupportedUploadFormatError("Upload contains no visible text")
    if len(normalized) > max_characters:
        raise UploadTextClassifiableLimitError(
            "Upload text exceeds the PromptGuard classification budget"
        )
    if len(normalized.encode("utf-8")) > max_output_bytes:
        raise UploadTextClassifiableLimitError(
            "Upload text exceeds the extracted output budget"
        )
    return ExtractionResult(
        title=None,
        author=None,
        date=None,
        raw_text=normalized,
        main_content=normalized,
        word_count=len(normalized.split()),
    )


def detect_upload_content_type(
    content_bytes: bytes, mime_hint: str | None = None
) -> str:
    """Return ``pdf`` or ``text`` for upload bytes, otherwise raise an error.

    A PDF magic prefix always wins. For non-PDF bytes, strict UTF-8 plus the
    explicit text-validity gate in :func:`extract_upload_text` is required.
    """
    if content_bytes.startswith(_PDF_MAGIC):
        return "pdf"
    extract_upload_text(content_bytes, mime_hint)
    return "text"
