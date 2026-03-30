"""Smart content extraction for summary mode.

Preserves high-signal content (statistics, quotes, references, lists,
tables, first/last paragraphs) and trims low-signal filler.  When
trafilatura succeeded, non-matching paragraphs are trimmed (trafilatura
already removed boilerplate).  When trafilatura failed (fallback path),
additional boilerplate heuristics are applied.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Compiled regex patterns -- preservation rules
# ---------------------------------------------------------------------------

_RE_PERCENTAGE = re.compile(r"\d+(\.\d+)?%")
_RE_PVALUE = re.compile(r"p\s*[<>=]\s*0\.\d+", re.IGNORECASE)
_RE_CI = re.compile(r"CI\s*[:=]?\s*[\[\(]", re.IGNORECASE)
_RE_DOLLAR = re.compile(r"\$[\d,]+(\.\d+)?")
_RE_QUOTE = re.compile(r'["\u201c].{10,}?["\u201d]')
_RE_REFERENCE = re.compile(
    r"according to|et al\.|published in|reported by|study by",
    re.IGNORECASE,
)
_RE_LIST_ITEM = re.compile(r"^\s*[-*]\s+|^\s*\d+\.\s+", re.MULTILINE)
_RE_TABLE_ROW = re.compile(r"\|.*\|")

# ---------------------------------------------------------------------------
# Compiled regex patterns -- boilerplate detection (fallback only)
# ---------------------------------------------------------------------------

_RE_COOKIE = re.compile(
    r"we use cookies|privacy policy|accept all|cookie settings",
    re.IGNORECASE,
)
_RE_AUTHOR_BIO = re.compile(
    r"about the author|written by .{3,30} is a",
    re.IGNORECASE,
)
_RE_RELATED = re.compile(
    r"you may also like|related articles|read more|recommended for you",
    re.IGNORECASE,
)
_RE_AD_COPY = re.compile(
    r"subscribe now|sign up for|limited time offer|click here",
    re.IGNORECASE,
)

_BOILERPLATE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (_RE_COOKIE, "cookie/privacy notices"),
    (_RE_AUTHOR_BIO, "author bios"),
    (_RE_RELATED, "related content"),
    (_RE_AD_COPY, "ads/promotions"),
]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _split_paragraphs(text: str) -> list[str]:
    """Split text into paragraphs on double-newline boundaries.

    Falls back to single-newline splitting when the text contains no
    double-newline separators (e.g. very short or poorly formatted pages).
    """
    paragraphs = [p.strip() for p in re.split(r"\n\n+", text)]
    paragraphs = [p for p in paragraphs if p]
    if len(paragraphs) <= 1 and "\n" in text:
        paragraphs = [p.strip() for p in text.split("\n")]
        paragraphs = [p for p in paragraphs if p]
    return paragraphs


def _should_preserve(paragraph: str, index: int, total: int) -> bool:
    """Return True if *paragraph* matches any preservation rule.

    A paragraph is preserved when it is the first or last paragraph, or
    when it contains statistics, quotes, references, list items, or
    table rows.
    """
    # First / last paragraph
    if index == 0 or index == total - 1:
        return True

    # Statistical / numerical evidence
    if _RE_PERCENTAGE.search(paragraph):
        return True
    if _RE_PVALUE.search(paragraph):
        return True
    if _RE_CI.search(paragraph):
        return True
    if _RE_DOLLAR.search(paragraph):
        return True

    # Quotes
    if _RE_QUOTE.search(paragraph):
        return True

    # Named references
    if _RE_REFERENCE.search(paragraph):
        return True

    # List items
    if _RE_LIST_ITEM.search(paragraph):
        return True

    # Table rows
    return bool(_RE_TABLE_ROW.search(paragraph))


def _classify_boilerplate(paragraph: str) -> str | None:
    """Classify *paragraph* as a boilerplate category, or None."""
    for pattern, category in _BOILERPLATE_PATTERNS:
        if pattern.search(paragraph):
            return category
    return None


def _build_truncation_notice(
    total_words: int,
    trimmed_sections: dict[str, int],
) -> str:
    """Build a human-readable truncation notice.

    Returns an empty string when nothing was trimmed.
    """
    if not trimmed_sections:
        return ""

    omitted_parts = [
        f"{name} ({count} words)" for name, count in trimmed_sections.items()
    ]
    omitted_str = ", ".join(omitted_parts)

    return (
        f"Content extracted from {total_words} word page. "
        "Summary preserves statistics, quotes, and referenced claims. "
        f"Omitted: {omitted_str}. "
        "Use extract_mode='full' to retrieve complete content."
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_summary(
    main_content: str,
    raw_text: str,
    title: str | None,
) -> tuple[str, str]:
    """Extract high-signal content for summary mode.

    Parameters
    ----------
    main_content:
        Boilerplate-free content from trafilatura (Stage 1).
    raw_text:
        Full flattened text from bs4 (Stage 1).
    title:
        Page title (currently unused, reserved for future heuristics).

    Returns
    -------
    A tuple of ``(extracted_body, truncation_notice)``.  The notice is
    an empty string when no content was trimmed.
    """
    if not main_content:
        return "", ""

    is_fallback = main_content == raw_text

    paragraphs = _split_paragraphs(main_content)
    total_words = sum(len(p.split()) for p in paragraphs)

    kept: list[str] = []
    trimmed_sections: dict[str, int] = {}

    for i, para in enumerate(paragraphs):
        if _should_preserve(para, i, len(paragraphs)):
            kept.append(para)
        elif is_fallback:
            # Fallback path: apply boilerplate heuristics
            category = _classify_boilerplate(para)
            if category is not None:
                trimmed_sections[category] = trimmed_sections.get(category, 0) + len(
                    para.split()
                )
            else:
                # Non-boilerplate, non-preserved content in fallback
                trimmed_sections["general content"] = trimmed_sections.get(
                    "general content", 0
                ) + len(para.split())
        else:
            # Trafilatura path: trim non-preserved paragraphs
            trimmed_sections["general content"] = trimmed_sections.get(
                "general content", 0
            ) + len(para.split())

    extracted = "\n\n".join(kept)
    notice = _build_truncation_notice(total_words, trimmed_sections)
    return extracted, notice
