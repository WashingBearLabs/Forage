"""Stage 1 -- HTML extraction and normalization.

Produces TWO outputs from raw HTML:

1. **raw_text** -- full flattened text (bs4) for security scanning (Stages 2-3).
2. **main_content** -- boilerplate-free main content (trafilatura) for Stage 4.

Both outputs pass through identical post-processing: invisible-Unicode
collapsing, UTF-8 normalization, and whitespace collapsing.
"""

from __future__ import annotations

import copy
import json
import re
import unicodedata
from dataclasses import dataclass
from typing import cast

from bs4 import BeautifulSoup, Comment, Tag
from bs4.element import NavigableString

try:
    import trafilatura
except ImportError:  # pragma: no cover - trafilatura is a declared dependency
    # Binding the module name to None (rather than a separate `_HAS_*` flag)
    # is what lets the call site's `if trafilatura is None` narrow the name for
    # a type checker: a boolean flag carries no such correlation, and the
    # `trafilatura.extract(...)` below would read as possibly-unbound.
    trafilatura = None

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# HTML elements stripped before raw-text extraction (security-sensitive)
_DANGEROUS_TAGS = frozenset(
    {
        "script",
        "style",
        "iframe",
        "meta",
        "link",
        "object",
        "embed",
        "form",
        "svg",
    }
)

# Invisible Unicode codepoints to collapse
_INVISIBLE_CHARS = frozenset(
    {
        "\u200b",  # zero-width space
        "\u200c",  # ZWNJ
        "\u200d",  # ZWJ
        "\u200e",  # LRM (bonus)
        "\u200f",  # RLM (bonus)
        "\u202e",  # RLO
        "\ufeff",  # zero-width no-break space / BOM
        "\u2060",  # word joiner
        "\u00ad",  # soft hyphen
    }
)

# str.translate table -- map each invisible char to None (delete)
_INVISIBLE_TABLE = str.maketrans({ch: None for ch in _INVISIBLE_CHARS})

# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    """Structured output of Stage 1 extraction."""

    title: str | None
    author: str | None
    date: str | None
    raw_text: str  # full flattened text for security scanning
    main_content: str  # trafilatura main content (or raw_text fallback)
    word_count: int  # counted on main_content


# ---------------------------------------------------------------------------
# Post-processing helpers
# ---------------------------------------------------------------------------


def _collapse_invisible(text: str) -> str:
    """Remove invisible Unicode characters."""
    return text.translate(_INVISIBLE_TABLE)


def _normalize_text(text: str) -> str:
    """UTF-8 normalize, collapse invisible chars, collapse whitespace."""
    # NFC normalization (canonical decomposition + canonical composition)
    text = unicodedata.normalize("NFC", text)
    # Remove invisible Unicode
    text = _collapse_invisible(text)
    # Collapse runs of whitespace within lines to single space
    text = re.sub(r"[^\S\n]+", " ", text)
    # Collapse 3+ consecutive newlines to double newline (preserve paragraphs)
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Strip leading/trailing whitespace from each line
    text = "\n".join(line.strip() for line in text.split("\n"))
    # Strip leading/trailing whitespace from entire text
    return text.strip()


def normalize_text(text: str) -> str:
    """Normalize extracted text with the shared Stage 1 post-processing rules."""
    return _normalize_text(text)


# ---------------------------------------------------------------------------
# Metadata extraction
# ---------------------------------------------------------------------------


def _extract_title(soup: BeautifulSoup) -> str | None:
    """Extract page title from <title> tag."""
    tag = soup.find("title")
    if tag and tag.string:
        title = tag.string.strip()
        return title if title else None
    return None


def _attr_text(element: Tag | NavigableString | None, name: str) -> str | None:
    """Return *element*'s ``name`` attribute as stripped text, else ``None``.

    Two shapes have to be filtered out before ``.strip()`` is safe, and both
    are things bs4 really returns: ``find()`` can hand back a
    ``NavigableString`` rather than a ``Tag``, and a multi-valued attribute
    (``class``, ``rel``) comes back as an ``AttributeValueList``, not a
    ``str``. The previous ``tag[name].strip()`` raised ``AttributeError`` on
    the second; here both fall through to the next extraction strategy, which
    is what the priority-ordered callers below already expect from a miss.
    """
    if not isinstance(element, Tag):
        return None
    value = element.get(name)
    # Absent or empty is a miss; a whitespace-only value still strips to "",
    # which is the pre-existing behaviour and is left alone deliberately.
    if not isinstance(value, str) or not value:
        return None
    return value.strip()


def _json_ld_documents(soup: BeautifulSoup) -> list[dict[str, object]]:
    """Return the JSON-LD ``<script>`` payloads that parse to an object.

    ``json.loads`` is typed as returning ``Any``, which would spread through
    every downstream ``.get()`` as an unknown type. Narrowing once here — to
    ``dict[str, object]``, the only shape the callers look at — keeps the
    strategies below honest about what they actually know.
    """
    documents: list[dict[str, object]] = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            parsed: object = json.loads(script.get_text() or "")
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, dict):
            documents.append(cast("dict[str, object]", parsed))
    return documents


def _extract_author(soup: BeautifulSoup) -> str | None:
    """Extract author via multiple strategies (priority order)."""
    # 1. <meta property="article:author">
    author = _attr_text(
        soup.find("meta", attrs={"property": "article:author"}), "content"
    )
    if author is not None:
        return author

    # 2. <meta name="author">
    author = _attr_text(soup.find("meta", attrs={"name": "author"}), "content")
    if author is not None:
        return author

    # 3. <a rel="author">
    a_tag = soup.find("a", attrs={"rel": "author"})
    if a_tag and a_tag.get_text(strip=True):
        return a_tag.get_text(strip=True)

    # 4. <span class="author">
    span = soup.find("span", class_="author")
    if span and span.get_text(strip=True):
        return span.get_text(strip=True)

    # 5. <div class="byline">
    div = soup.find("div", class_="byline")
    if div and div.get_text(strip=True):
        return div.get_text(strip=True)

    # 6. JSON-LD @type: Article -> author.name
    for data in _json_ld_documents(soup):
        if data.get("@type") != "Article":
            continue
        candidate = data.get("author")
        if isinstance(candidate, dict):
            name = cast("dict[str, object]", candidate).get("name")
            if name:
                return str(name).strip()
        elif isinstance(candidate, str):
            return candidate.strip()

    return None


def _extract_date(soup: BeautifulSoup) -> str | None:
    """Extract publication date via multiple strategies (priority order)."""
    # 1. <meta property="article:published_time">
    date = _attr_text(
        soup.find("meta", attrs={"property": "article:published_time"}), "content"
    )
    if date is not None:
        return date

    # 2. <meta name="date">
    date = _attr_text(soup.find("meta", attrs={"name": "date"}), "content")
    if date is not None:
        return date

    # 3. <time datetime="...">
    date = _attr_text(soup.find("time", attrs={"datetime": True}), "datetime")
    if date is not None:
        return date

    # 4. JSON-LD datePublished
    for data in _json_ld_documents(soup):
        date_pub = data.get("datePublished")
        if date_pub:
            return str(date_pub).strip()

    return None


# ---------------------------------------------------------------------------
# Raw text extraction (bs4)
# ---------------------------------------------------------------------------


def _extract_raw_text(soup: BeautifulSoup) -> str:
    """Strip dangerous elements and extract full flattened text."""
    # Work on a copy to avoid mutating the original soup.
    # Use copy.copy instead of re-parsing via str(soup) to avoid
    # the overhead of serialisation + re-parse.
    soup = copy.copy(soup)

    # Remove dangerous tags
    for tag_name in _DANGEROUS_TAGS:
        for tag in soup.find_all(tag_name):
            tag.decompose()

    # Remove HTML comments
    for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
        comment.extract()

    return soup.get_text(separator="\n")


# ---------------------------------------------------------------------------
# Main content extraction (trafilatura)
# ---------------------------------------------------------------------------


def _extract_main_content(html: str, url: str | None = None) -> str | None:
    """Use trafilatura to extract main content with boilerplate removed.

    Returns ``None`` when trafilatura is not installed or fails to extract.
    """
    if trafilatura is None:
        return None

    result = trafilatura.extract(
        html,
        url=url,
        include_tables=True,
        include_links=False,
        include_comments=False,
    )
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_html(html: str, url: str | None = None) -> ExtractionResult:
    """Extract text and metadata from HTML content.

    Parameters
    ----------
    html:
        Raw HTML string.
    url:
        Optional source URL (improves trafilatura heuristics).

    Returns
    -------
    ExtractionResult with both raw_text and main_content.
    """
    soup = BeautifulSoup(html, "lxml")

    # -- Metadata (extracted before stripping) --
    title = _extract_title(soup)
    author = _extract_author(soup)
    date = _extract_date(soup)

    # -- Dual extraction --
    raw_text = _normalize_text(_extract_raw_text(soup))
    main_content_raw = _extract_main_content(html, url=url)

    if main_content_raw is not None:
        main_content = _normalize_text(main_content_raw)
    else:
        # Fallback: use raw_text when trafilatura fails
        main_content = raw_text

    # -- Word count on main_content --
    word_count = len(main_content.split()) if main_content else 0

    return ExtractionResult(
        title=title,
        author=author,
        date=date,
        raw_text=raw_text,
        main_content=main_content,
        word_count=word_count,
    )
