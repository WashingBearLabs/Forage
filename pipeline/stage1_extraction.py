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
from bs4.element import CData, NavigableString

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

# Elements that separate text in the inline scan form. Closed on purpose: every
# other element (``b``, ``span``, ``wbr``, ``font``, custom elements) is joined
# into its surrounding text, so a tag splitting a trigger word cannot hide it.
_INLINE_SCAN_BLOCK_TAGS = frozenset(
    {
        "address",
        "article",
        "aside",
        "blockquote",
        "body",
        "br",
        "caption",
        "dd",
        "details",
        "dialog",
        "div",
        "dl",
        "dt",
        "fieldset",
        "figcaption",
        "figure",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "head",
        "header",
        "hr",
        "html",
        "legend",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "summary",
        "table",
        "tbody",
        "td",
        "tfoot",
        "th",
        "thead",
        "title",
        "tr",
        "ul",
    }
)

_BLOCK_CLOSE = object()

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
    # Scan-only inline-joined text, built by ``extract_html(with_inline=True)``.
    # The orchestrator scans it inside the stage-1 thread and clears it, so it
    # never leaves that thread.
    scan_text_inline: str | None = None
    # Whether ``main_content`` is the flattened-text fallback rather than
    # trafilatura output. ``None`` means "infer by ``main_content == raw_text``"
    # (PDF, upload, test constructors); ``extract_html`` sets it only when the
    # visibility pass removed something, because pruning breaks that equality.
    main_content_is_fallback: bool | None = None


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


def _extract_inline_text(soup: BeautifulSoup) -> str:
    """Flatten *soup* with every non-block element joined into its text.

    One iterative walk in document order: no tree mutation, no ``unwrap()``,
    no ``smooth()``, so the cost is linear in the node count. Dangerous
    subtrees and non-text nodes are skipped exactly as ``_extract_raw_text``
    drops them; entering and leaving a block element emits a newline.
    """
    parts: list[str] = []
    stack: list[object] = [soup]
    while stack:
        node = stack.pop()
        if node is _BLOCK_CLOSE:
            parts.append("\n")
        elif isinstance(node, Tag):
            if node.name in _DANGEROUS_TAGS:
                continue
            if node.name in _INLINE_SCAN_BLOCK_TAGS:
                parts.append("\n")
                stack.append(_BLOCK_CLOSE)
            stack.extend(reversed(node.contents))
        elif type(node) in (NavigableString, CData):
            parts.append(str(node))
    return _normalize_text("".join(parts))


# ---------------------------------------------------------------------------
# Visibility pass (Forage-owned, inline signals only)
# ---------------------------------------------------------------------------

# Offsets and indents of at least this many pixels off-screen count as hidden.
_OFFSCREEN_PX = -999.0
# A style value longer than this is not a length or keyword we recognise.
_MAX_STYLE_TOKEN = 64
# Units that stay non-zero under a zero-sized parent (absolute or root-relative).
_RESHOW_FONT_UNITS = frozenset({"px", "pt", "pc", "cm", "mm", "in", "q", "rem"})


def _parse_style(style: str) -> dict[str, str]:
    """Parse an inline ``style`` value into ``{property: value}``, linearly.

    Split on ``;`` then the first ``:``; names and values are lowercased and
    stripped, a trailing ``!important`` is dropped, a repeated property is won
    by its last declaration, and a declaration without a ``:`` or a name is
    ignored.
    """
    declarations: dict[str, str] = {}
    for declaration in style.split(";"):
        name, separator, value = declaration.partition(":")
        if not separator:
            continue
        name = name.strip().lower()
        if not name:
            continue
        value = value.strip().lower()
        if value.endswith("!important"):
            value = value[: -len("!important")].rstrip()
        declarations[name] = value
    return declarations


def _split_length(value: str) -> tuple[float, str] | None:
    """Split ``"-12.5px"`` into ``(-12.5, "px")``; ``None`` if not a length."""
    if not value or len(value) > _MAX_STYLE_TOKEN:
        return None
    index = 0
    if value[0] in "+-":
        index = 1
    digits_start = index
    seen_dot = False
    while index < len(value):
        char = value[index]
        if char == ".":
            if seen_dot:
                return None
            seen_dot = True
        elif not char.isascii() or not char.isdigit():
            break
        index += 1
    number = value[digits_start:index]
    if not number or number == ".":
        return None
    unit = value[index:]
    if unit and not unit.isalpha() and unit != "%":
        return None
    return float(value[:index]), unit


def _is_zero(value: str | None) -> bool:
    """True for a zero length in any unit (``0``, ``0.0``, ``0%``, ``0em``)."""
    if value is None:
        return False
    parsed = _split_length(value)
    return parsed is not None and parsed[0] == 0


def _is_offscreen_px(value: str | None) -> bool:
    """True for a pixel length at or beyond the off-screen threshold."""
    if value is None:
        return False
    parsed = _split_length(value)
    return parsed is not None and parsed[1] == "px" and parsed[0] <= _OFFSCREEN_PX


def _is_zero_clip(value: str | None) -> bool:
    """True for ``rect(...)`` with all four components zero, any separator."""
    if value is None or not value.startswith("rect(") or not value.endswith(")"):
        return False
    parts = value[len("rect(") : -1].replace(",", " ").split()
    return len(parts) == 4 and all(_is_zero(part) for part in parts)


def _is_non_overridable_hidden(tag: Tag, style: dict[str, str]) -> bool:
    """Whether *tag*'s whole subtree is invisible regardless of descendants."""
    hidden = tag.get("hidden")
    if hidden is not None and str(hidden).strip().lower() != "until-found":
        return True
    aria_hidden = tag.get("aria-hidden")
    if isinstance(aria_hidden, str) and aria_hidden.strip().lower() == "true":
        return True
    if not style:
        return False
    if style.get("display") == "none":
        return True
    opacity = style.get("opacity")
    if opacity is not None:
        parsed = _split_length(opacity)
        if parsed is not None and parsed[0] == 0 and parsed[1] in ("", "%"):
            return True
    if _is_zero_clip(style.get("clip")):
        return True
    if _is_offscreen_px(style.get("text-indent")):
        return True
    if style.get("position") in ("absolute", "fixed") and (
        _is_offscreen_px(style.get("left")) or _is_offscreen_px(style.get("top"))
    ):
        return True
    return style.get("overflow") == "hidden" and (
        _is_zero(style.get("width")) or _is_zero(style.get("height"))
    )


def _inherit_visibility(style: dict[str, str], hidden: bool) -> bool:
    """The element's computed ``visibility`` hidden state, given its parent's."""
    value = style.get("visibility")
    if value == "visible":
        return False
    if value in ("hidden", "collapse"):
        return True
    return hidden


def _inherit_font_zero(style: dict[str, str], zero: bool) -> bool:
    """The element's computed zero-``font-size`` state, given its parent's.

    Zero in any unit zeroes it; a non-zero absolute or root-relative size
    re-shows it; a relative size (``em``, ``%``, keywords) computes against
    the parent and so inherits the parent's state.
    """
    value = style.get("font-size")
    if value is None:
        return zero
    parsed = _split_length(value)
    if parsed is None:
        return zero
    number, unit = parsed
    if number == 0:
        return True
    if unit in _RESHOW_FONT_UNITS:
        return False
    return zero


def _has_visibility_signal(body: Tag) -> bool:
    """Read-only pre-check: does a body descendant carry a candidate attribute?"""
    for node in body.descendants:
        if isinstance(node, Tag) and (
            node.has_attr("hidden")
            or node.has_attr("aria-hidden")
            or node.has_attr("style")
        ):
            return True
    return False


def _prune_hidden(soup: BeautifulSoup) -> tuple[BeautifulSoup, bool]:
    """Remove text a browser would not show; return ``(soup, pruned)``.

    Best effort, inline signals only (no stylesheet or class resolution), and
    only descendants of ``<body>``. The shared *soup* is never mutated: when a
    candidate attribute exists the work happens on ``copy.copy(soup)``, and
    when none does the original is returned untouched with ``pruned=False``.
    Traversal is iterative so nesting depth cannot exhaust the stack.
    """
    original_body = soup.body
    if original_body is None or not _has_visibility_signal(original_body):
        return soup, False
    pruned_soup = copy.copy(soup)
    body = pruned_soup.body
    if body is None:  # pragma: no cover - a copy of a soup with a body has one
        return soup, False

    pruned = False
    # (tag, inherited visibility-hidden, inherited font-size-zero)
    stack: list[tuple[Tag, bool, bool]] = [
        (child, False, False)
        for child in reversed(body.contents)
        if isinstance(child, Tag)
    ]
    while stack:
        tag, inherited_hidden, inherited_zero = stack.pop()
        if tag.name in _DANGEROUS_TAGS:
            continue
        style = _parse_style(str(tag.get("style", ""))) if tag.has_attr("style") else {}
        if _is_non_overridable_hidden(tag, style):
            tag.decompose()
            pruned = True
            continue
        hidden = _inherit_visibility(style, inherited_hidden)
        zero = _inherit_font_zero(style, inherited_zero)
        children = list(tag.contents)
        if hidden or zero:
            for child in children:
                if isinstance(child, NavigableString):
                    if str(child).strip():
                        pruned = True
                    child.extract()
        stack.extend(
            (child, hidden, zero)
            for child in reversed(children)
            if isinstance(child, Tag)
        )
    return pruned_soup, pruned


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


def extract_html(
    html: str,
    url: str | None = None,
    *,
    with_inline: bool = False,
    prune_hidden: bool = True,
) -> ExtractionResult:
    """Extract text and metadata from HTML content.

    Parameters
    ----------
    html:
        Raw HTML string.
    url:
        Optional source URL (improves trafilatura heuristics).
    with_inline:
        Also build the inline-joined scan text from this same soup (no second
        parse) into ``scan_text_inline``. ``raw_text`` is unaffected.
    prune_hidden:
        Run the visibility pass over the served ``main_content``. ``raw_text``,
        the metadata and the inline scan text always read the unpruned soup, so
        callers that only read ``raw_text`` pass ``False`` to skip the work.

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
    pruned_soup, pruned = _prune_hidden(soup) if prune_hidden else (soup, False)
    # Unpruned pages hand trafilatura the original string: re-serialising every
    # page would move benign bodies.
    main_content_raw = _extract_main_content(
        str(pruned_soup) if pruned else html, url=url
    )

    is_fallback: bool | None = None
    if main_content_raw is not None:
        main_content = _normalize_text(main_content_raw)
        if pruned:
            is_fallback = False
    elif pruned:
        # Fallback: the flattened text of what remains visible
        main_content = _normalize_text(_extract_raw_text(pruned_soup))
        is_fallback = True
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
        scan_text_inline=_extract_inline_text(soup) if with_inline else None,
        main_content_is_fallback=is_fallback,
    )
