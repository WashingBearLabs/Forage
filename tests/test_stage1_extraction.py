"""Tests for Stage 1 -- HTML extraction and normalization (US-003)."""

from __future__ import annotations

from pipeline.stage1_extraction import (
    ExtractionResult,
    _collapse_invisible,
    _normalize_text,
    extract_html,
)
from tests.fakes import assert_frozen

# ---------------------------------------------------------------------------
# Sample HTML fixtures
# ---------------------------------------------------------------------------

SAMPLE_ARTICLE_HTML = """\
<!DOCTYPE html>
<html>
<head>
    <title>Test Article Title</title>
    <meta property="article:author" content="Jane Doe">
    <meta property="article:published_time" content="2026-03-15T10:00:00Z">
    <link rel="stylesheet" href="/style.css">
    <style>.hidden { display: none; }</style>
    <script>alert('xss');</script>
</head>
<body>
    <nav><a href="/">Home</a> | <a href="/about">About</a></nav>
    <header><h1>Test Article Title</h1></header>
    <article>
        <p>This is the main article content. It contains several paragraphs
        of meaningful text that trafilatura should identify as main content.</p>
        <p>Second paragraph with more details about the topic at hand.
        This gives trafilatura enough signal to classify it as the main body.</p>
        <p>Third paragraph continues the discussion with additional information
        and context that makes this a substantive article body.</p>
    </article>
    <iframe src="https://evil.com/tracker"></iframe>
    <aside>Related Articles: <a href="/other">Other post</a></aside>
    <footer>Copyright 2026 Example Corp. Cookie settings. Privacy policy.</footer>
    <!-- This is a tracking comment with payload -->
    <meta name="generator" content="WordPress">
</body>
</html>
"""

MINIMAL_HTML = "<html><body><p>Hello world.</p></body></html>"

HTML_WITH_SCRIPTS_STYLES_IFRAMES = """\
<html>
<head><title>Dirty Page</title></head>
<body>
<script>var x = 'malicious code';</script>
<style>.trick { position: absolute; left: -9999px; }</style>
<p>Visible content here.</p>
<iframe src="https://tracker.example.com/pixel"></iframe>
<p>More visible content.</p>
<!-- secret comment payload -->
<link rel="prefetch" href="https://evil.com">
<meta http-equiv="refresh" content="0;url=https://phish.com">
</body>
</html>
"""

HTML_WITH_UNICODE_ATTACKS = (
    "<html>\n<body>\n"
    "<p>Normal text\u200b\u200c\u200d with\u202e"
    " invisible\ufeff chars\u2060 and\u00ad hyphens.</p>\n"
    "</body>\n</html>\n"
)

HTML_WITH_JSONLD_METADATA = """\
<html>
<head>
    <title>JSON-LD Article</title>
    <script type="application/ld+json">
    {
        "@type": "Article",
        "author": {"name": "Bob Smith"},
        "datePublished": "2026-01-20"
    }
    </script>
</head>
<body>
<article>
<p>Article content from JSON-LD page with structured data markup.</p>
</article>
</body>
</html>
"""

HTML_META_AUTHOR = """\
<html>
<head>
    <title>Meta Author</title>
    <meta name="author" content="Alice Johnson">
    <meta name="date" content="2026-02-10">
</head>
<body><p>Content with meta author.</p></body>
</html>
"""

HTML_A_REL_AUTHOR = """\
<html>
<body>
<p>Written by <a rel="author">Carol Williams</a></p>
<time datetime="2026-05-01">May 1, 2026</time>
<p>Article body text.</p>
</body>
</html>
"""

HTML_SPAN_AUTHOR = """\
<html>
<body>
<p>By <span class="author">Dan Brown</span></p>
<p>Body text.</p>
</body>
</html>
"""

HTML_BYLINE_AUTHOR = """\
<html>
<body>
<div class="byline">Emily Zhang</div>
<p>Body text.</p>
</body>
</html>
"""

HTML_EXCESSIVE_WHITESPACE = """\
<html>
<body>
<p>First paragraph.</p>


<p>Second paragraph after lots of blank lines.</p>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Post-processing tests
# ---------------------------------------------------------------------------


class TestNormalization:
    """Test text normalization and Unicode collapsing."""

    def test_collapse_invisible_unicode(self) -> None:
        text = "hello\u200bworld\u200c\u200d\u202e\ufeff\u2060\u00ad"
        result = _collapse_invisible(text)
        assert result == "helloworld"

    def test_normalize_text_collapses_whitespace(self) -> None:
        text = "hello   world   foo"
        result = _normalize_text(text)
        assert result == "hello world foo"

    def test_normalize_text_collapses_newlines(self) -> None:
        text = "para1\n\n\n\n\npara2"
        result = _normalize_text(text)
        assert result == "para1\n\npara2"

    def test_normalize_text_preserves_double_newline(self) -> None:
        text = "para1\n\npara2"
        result = _normalize_text(text)
        assert result == "para1\n\npara2"

    def test_normalize_text_strips_line_edges(self) -> None:
        text = "  hello  \n  world  "
        result = _normalize_text(text)
        assert result == "hello\nworld"

    def test_normalize_text_nfc(self) -> None:
        # e + combining acute accent -> e-acute (NFC)
        text = "caf\u0065\u0301"
        result = _normalize_text(text)
        assert "\u00e9" in result  # NFC composed form


# ---------------------------------------------------------------------------
# Dangerous element stripping
# ---------------------------------------------------------------------------


class TestDangerousElementStripping:
    """Test that scripts, styles, iframes, meta, link, and comments are stripped."""

    def test_scripts_stripped(self) -> None:
        result = extract_html(HTML_WITH_SCRIPTS_STYLES_IFRAMES)
        assert "malicious code" not in result.raw_text
        assert "var x" not in result.raw_text

    def test_styles_stripped(self) -> None:
        result = extract_html(HTML_WITH_SCRIPTS_STYLES_IFRAMES)
        assert ".trick" not in result.raw_text
        assert "position: absolute" not in result.raw_text

    def test_iframes_stripped(self) -> None:
        result = extract_html(HTML_WITH_SCRIPTS_STYLES_IFRAMES)
        assert "tracker.example.com" not in result.raw_text

    def test_comments_stripped(self) -> None:
        result = extract_html(HTML_WITH_SCRIPTS_STYLES_IFRAMES)
        assert "secret comment payload" not in result.raw_text

    def test_meta_stripped(self) -> None:
        result = extract_html(HTML_WITH_SCRIPTS_STYLES_IFRAMES)
        assert "phish.com" not in result.raw_text

    def test_link_stripped(self) -> None:
        result = extract_html(HTML_WITH_SCRIPTS_STYLES_IFRAMES)
        assert "prefetch" not in result.raw_text

    def test_visible_content_preserved(self) -> None:
        result = extract_html(HTML_WITH_SCRIPTS_STYLES_IFRAMES)
        assert "Visible content here." in result.raw_text
        assert "More visible content." in result.raw_text

    def test_title_extracted_from_dirty_page(self) -> None:
        result = extract_html(HTML_WITH_SCRIPTS_STYLES_IFRAMES)
        assert result.title == "Dirty Page"


# ---------------------------------------------------------------------------
# Unicode attack handling
# ---------------------------------------------------------------------------


class TestUnicodeHandling:
    """Test invisible Unicode collapsing."""

    def test_invisible_chars_removed_from_raw_text(self) -> None:
        result = extract_html(HTML_WITH_UNICODE_ATTACKS)
        # Zero-width space should be gone
        assert "\u200b" not in result.raw_text
        assert "\u200c" not in result.raw_text
        assert "\u200d" not in result.raw_text
        assert "\u202e" not in result.raw_text
        assert "\ufeff" not in result.raw_text
        assert "\u2060" not in result.raw_text
        assert "\u00ad" not in result.raw_text

    def test_invisible_chars_removed_from_main_content(self) -> None:
        result = extract_html(HTML_WITH_UNICODE_ATTACKS)
        assert "\u200b" not in result.main_content
        assert "\u202e" not in result.main_content

    def test_visible_text_preserved_after_unicode_strip(self) -> None:
        result = extract_html(HTML_WITH_UNICODE_ATTACKS)
        assert "Normal text" in result.raw_text
        assert "invisible" in result.raw_text


# ---------------------------------------------------------------------------
# Metadata extraction
# ---------------------------------------------------------------------------


class TestMetadataExtraction:
    """Test title, author, date extraction from various HTML patterns."""

    def test_title_extracted(self) -> None:
        result = extract_html(SAMPLE_ARTICLE_HTML)
        assert result.title == "Test Article Title"

    def test_article_author_meta(self) -> None:
        result = extract_html(SAMPLE_ARTICLE_HTML)
        assert result.author == "Jane Doe"

    def test_article_published_time(self) -> None:
        result = extract_html(SAMPLE_ARTICLE_HTML)
        assert result.date == "2026-03-15T10:00:00Z"

    def test_meta_name_author(self) -> None:
        result = extract_html(HTML_META_AUTHOR)
        assert result.author == "Alice Johnson"

    def test_meta_name_date(self) -> None:
        result = extract_html(HTML_META_AUTHOR)
        assert result.date == "2026-02-10"

    def test_a_rel_author(self) -> None:
        result = extract_html(HTML_A_REL_AUTHOR)
        assert result.author == "Carol Williams"

    def test_time_datetime(self) -> None:
        result = extract_html(HTML_A_REL_AUTHOR)
        assert result.date == "2026-05-01"

    def test_span_author(self) -> None:
        result = extract_html(HTML_SPAN_AUTHOR)
        assert result.author == "Dan Brown"

    def test_byline_author(self) -> None:
        result = extract_html(HTML_BYLINE_AUTHOR)
        assert result.author == "Emily Zhang"

    def test_jsonld_author(self) -> None:
        result = extract_html(HTML_WITH_JSONLD_METADATA)
        assert result.author == "Bob Smith"

    def test_jsonld_date(self) -> None:
        result = extract_html(HTML_WITH_JSONLD_METADATA)
        assert result.date == "2026-01-20"

    def test_no_metadata_returns_none(self) -> None:
        result = extract_html(MINIMAL_HTML)
        assert result.author is None
        assert result.date is None


# ---------------------------------------------------------------------------
# Dual extraction (raw_text vs main_content)
# ---------------------------------------------------------------------------


class TestDualExtraction:
    """Test that both raw_text and main_content are produced."""

    def test_both_outputs_present(self) -> None:
        result = extract_html(SAMPLE_ARTICLE_HTML)
        assert result.raw_text
        assert result.main_content

    def test_raw_text_includes_nav_footer(self) -> None:
        """raw_text should include everything (nav, footer, etc.)."""
        result = extract_html(SAMPLE_ARTICLE_HTML)
        # raw_text includes navigational elements
        assert "Home" in result.raw_text

    def test_result_is_extraction_result(self) -> None:
        result = extract_html(SAMPLE_ARTICLE_HTML)
        assert isinstance(result, ExtractionResult)

    def test_word_count_based_on_main_content(self) -> None:
        result = extract_html(SAMPLE_ARTICLE_HTML)
        expected = len(result.main_content.split())
        assert result.word_count == expected


# ---------------------------------------------------------------------------
# Trafilatura fallback
# ---------------------------------------------------------------------------


class TestTrafilaturaFallback:
    """Test fallback to raw_text when trafilatura returns None."""

    def test_minimal_html_uses_fallback(self) -> None:
        """Trafilatura may fail on very minimal HTML -- raw_text is used."""
        result = extract_html(MINIMAL_HTML)
        # Either trafilatura extracted something, or we fell back
        assert result.main_content  # should never be empty
        # If fallback occurred, main_content == raw_text
        if "Hello world." in result.raw_text:
            assert "Hello world." in result.main_content


# ---------------------------------------------------------------------------
# Whitespace normalization
# ---------------------------------------------------------------------------


class TestWhitespaceNormalization:
    """Test that excessive whitespace is properly collapsed."""

    def test_excessive_newlines_collapsed(self) -> None:
        result = extract_html(HTML_EXCESSIVE_WHITESPACE)
        # Should not have more than 2 consecutive newlines
        assert "\n\n\n" not in result.raw_text

    def test_content_preserved_after_collapse(self) -> None:
        result = extract_html(HTML_EXCESSIVE_WHITESPACE)
        assert "First paragraph." in result.raw_text
        assert "Second paragraph" in result.raw_text


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Test edge cases and robustness."""

    def test_empty_html(self) -> None:
        result = extract_html("")
        assert result.raw_text == "" or result.raw_text is not None
        assert result.word_count >= 0

    def test_html_with_only_scripts(self) -> None:
        html = "<html><body><script>evil();</script></body></html>"
        result = extract_html(html)
        assert "evil" not in result.raw_text

    def test_url_parameter_accepted(self) -> None:
        """extract_html should accept optional url parameter."""
        result = extract_html(MINIMAL_HTML, url="https://example.com/page")
        assert isinstance(result, ExtractionResult)

    def test_title_none_when_missing(self) -> None:
        html = "<html><body><p>No title here.</p></body></html>"
        result = extract_html(html)
        assert result.title is None

    def test_frozen_dataclass(self) -> None:
        result = extract_html(MINIMAL_HTML)
        assert_frozen(result, "title", "new title")
