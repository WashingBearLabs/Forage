"""Tests for smart extraction module (US-008).

Covers preservation rules (statistics, quotes, references, lists, tables,
first/last paragraphs), fallback boilerplate trimming, truncation notice
generation, and edge cases.
"""

from __future__ import annotations

from pipeline.smart_extraction import (
    _should_preserve,
    _split_paragraphs,
    extract_summary,
)

# ---------------------------------------------------------------------------
# Helper to build multi-paragraph text
# ---------------------------------------------------------------------------


def _join(*paragraphs: str) -> str:
    return "\n\n".join(paragraphs)


# ---------------------------------------------------------------------------
# Research article sample
# ---------------------------------------------------------------------------

_RESEARCH_ARTICLE = _join(
    "This study examines the effect of sleep duration on cognitive performance.",
    "Participants were recruited from three universities across the country.",
    "Results showed participants sleeping < 6 hours scored 23.5% lower.",
    "The effect was statistically significant (p < 0.001) across all age groups.",
    "Confidence intervals were narrow (CI: [0.18, 0.29]) indicating robust estimates.",
    "Previous work by Smith et al. found similar patterns in adolescent populations.",
    "Methodological limitations include self-reported sleep durations.",
    "These findings suggest that sleep interventions could improve academic outcomes.",
)

# ---------------------------------------------------------------------------
# News article sample
# ---------------------------------------------------------------------------

_NEWS_ARTICLE = _join(
    "Tech giant announces $4.5 billion investment in renewable energy.",
    "The company plans to build solar farms across three states over the next decade.",
    '"This is the largest corporate clean energy commitment," said CEO Jane Doe.',
    "Industry analysts noted the move could create approximately 50,000 new jobs.",
    "The announcement follows a broader trend of corporate sustainability pledges.",
    "Environmental groups have praised the initiative as a positive step forward.",
    "You may also like: Top 10 green companies of 2026.",
    "According to the IEA, corporate renewables investment grew 18%.",
)

# ---------------------------------------------------------------------------
# Blog post with ads (fallback -- no trafilatura)
# ---------------------------------------------------------------------------

_BLOG_FALLBACK = _join(
    "5 Tips for Better Remote Work Productivity.",
    "We use cookies to improve your experience. Accept all cookies.",
    "- Use a dedicated workspace separate from your living area.",
    "- Set clear boundaries with family members during work hours.",
    "- Take regular breaks every 90 minutes to maintain focus.",
    "About the author: Sarah Johnson is a productivity consultant.",
    "Subscribe now to our newsletter for weekly tips!",
    "You may also like related articles on focus techniques.",
    "Remote work adoption increased by 35% in the past year.",
)

# ---------------------------------------------------------------------------
# Clean academic page (all paragraphs contain signals)
# ---------------------------------------------------------------------------

_CLEAN_ACADEMIC = _join(
    "Meta-analysis of 47 randomized controlled trials on meditation and anxiety.",
    "Effect size was d = 0.58 (95% CI: [0.42, 0.74]), p < 0.001.",
    "- Mindfulness-based stress reduction: d = 0.62",
    "- Transcendental meditation: d = 0.53",
    "- Loving-kindness meditation: d = 0.49",
    "According to WHO, anxiety disorders affect 4% of the population.",
    "Study published in The Lancet Psychiatry, 2026.",
)


# ---------------------------------------------------------------------------
# Preservation rule tests
# ---------------------------------------------------------------------------


class TestPreservationRules:
    """Verify individual preservation rules via _should_preserve."""

    def test_first_paragraph_preserved(self):
        assert _should_preserve("Any content here", 0, 5) is True

    def test_last_paragraph_preserved(self):
        assert _should_preserve("Any content here", 4, 5) is True

    def test_percentage_preserved(self):
        assert _should_preserve("Scores improved by 23.5% overall", 2, 5) is True

    def test_pvalue_preserved(self):
        assert _should_preserve("The result was significant (p < 0.05)", 2, 5) is True

    def test_pvalue_equals_preserved(self):
        assert _should_preserve("We found p = 0.032 for the main effect", 2, 5) is True

    def test_confidence_interval_preserved(self):
        assert _should_preserve("The CI: [0.18, 0.29] was narrow", 2, 5) is True

    def test_dollar_amount_preserved(self):
        assert _should_preserve("The company invested $4,500,000", 2, 5) is True

    def test_quoted_text_preserved(self):
        text = '"This is a significant finding for the field," said Dr. Smith.'
        assert _should_preserve(text, 2, 5) is True

    def test_smart_quotes_preserved(self):
        text = "\u201cThis represents a major breakthrough,\u201d said the researcher."
        assert _should_preserve(text, 2, 5) is True

    def test_short_quote_not_preserved(self):
        # Quotes shorter than 10 chars should not trigger preservation
        text = 'He said "yes" to the proposal and the meeting concluded.'
        # "yes" is only 3 chars -- should not match
        assert _should_preserve(text, 2, 5) is False

    def test_named_reference_according_to(self):
        assert (
            _should_preserve("According to recent surveys, usage is up", 2, 5) is True
        )

    def test_named_reference_et_al(self):
        assert _should_preserve("Smith et al. reported similar findings", 2, 5) is True

    def test_named_reference_published_in(self):
        assert _should_preserve("Results published in Nature last month", 2, 5) is True

    def test_named_reference_reported_by(self):
        assert (
            _should_preserve("As reported by the WHO in their annual report", 2, 5)
            is True
        )

    def test_named_reference_study_by(self):
        assert (
            _should_preserve("A study by Harvard researchers confirmed this", 2, 5)
            is True
        )

    def test_list_item_dash(self):
        assert _should_preserve("- First key finding from the analysis", 2, 5) is True

    def test_list_item_asterisk(self):
        assert _should_preserve("* Another important data point", 2, 5) is True

    def test_list_item_numbered(self):
        assert (
            _should_preserve("1. The primary conclusion of the research", 2, 5) is True
        )

    def test_table_row(self):
        assert _should_preserve("| Category | Count | Percentage |", 2, 5) is True

    def test_generic_paragraph_not_preserved(self):
        text = "The weather was nice on the day of the experiment."
        assert _should_preserve(text, 2, 5) is False


# ---------------------------------------------------------------------------
# Research article extraction
# ---------------------------------------------------------------------------


class TestResearchArticle:
    """Smart extraction on a sample research article."""

    def test_statistics_preserved(self):
        body, _ = extract_summary(_RESEARCH_ARTICLE, "different raw", None)
        assert "23.5%" in body
        assert "p < 0.001" in body
        assert "CI: [0.18, 0.29]" in body

    def test_first_and_last_paragraphs_preserved(self):
        body, _ = extract_summary(_RESEARCH_ARTICLE, "different raw", None)
        assert "This study examines" in body
        assert "sleep interventions" in body

    def test_named_reference_preserved(self):
        body, _ = extract_summary(_RESEARCH_ARTICLE, "different raw", None)
        assert "et al." in body

    def test_filler_trimmed(self):
        body, _ = extract_summary(_RESEARCH_ARTICLE, "different raw", None)
        # "Participants were recruited" has no signal markers
        assert "Participants were recruited" not in body
        # "Methodological limitations" has no signal markers
        assert "Methodological limitations" not in body

    def test_truncation_notice_generated(self):
        _, notice = extract_summary(_RESEARCH_ARTICLE, "different raw", None)
        assert "general content" in notice
        assert "word page" in notice
        assert "extract_mode='full'" in notice


# ---------------------------------------------------------------------------
# News article extraction
# ---------------------------------------------------------------------------


class TestNewsArticle:
    """Smart extraction on a news article (trafilatura path)."""

    def test_dollar_amount_preserved(self):
        body, _ = extract_summary(_NEWS_ARTICLE, "different raw", None)
        assert "$4.5 billion" in body

    def test_quoted_text_preserved(self):
        body, _ = extract_summary(_NEWS_ARTICLE, "different raw", None)
        assert "largest corporate clean energy commitment" in body

    def test_named_reference_preserved(self):
        body, _ = extract_summary(_NEWS_ARTICLE, "different raw", None)
        assert "According to the IEA" in body

    def test_related_content_not_trimmed_in_trafilatura_path(self):
        """In the trafilatura path, 'You may also like' is trimmed as
        general non-signal content, but not classified as boilerplate."""
        body, notice = extract_summary(_NEWS_ARTICLE, "different raw", None)
        # The line has no preservation signal, so it gets trimmed
        assert "You may also like" not in body
        # But it's categorised as general content, not related content
        assert "general content" in notice

    def test_percentage_preserved(self):
        body, _ = extract_summary(_NEWS_ARTICLE, "different raw", None)
        assert "18%" in body


# ---------------------------------------------------------------------------
# Blog post with ads (fallback path)
# ---------------------------------------------------------------------------


class TestBlogFallback:
    """Smart extraction on a blog post where trafilatura failed."""

    def test_cookie_notice_trimmed(self):
        """Cookie notice in the middle is trimmed in fallback path."""
        body, notice = extract_summary(_BLOG_FALLBACK, _BLOG_FALLBACK, None)
        assert "We use cookies" not in body
        assert "cookie/privacy notices" in notice

    def test_list_items_preserved(self):
        body, _ = extract_summary(_BLOG_FALLBACK, _BLOG_FALLBACK, None)
        assert "- Use a dedicated workspace" in body
        assert "- Set clear boundaries" in body
        assert "- Take regular breaks" in body

    def test_author_bio_trimmed(self):
        body, notice = extract_summary(_BLOG_FALLBACK, _BLOG_FALLBACK, None)
        assert "About the author" not in body
        assert "author bios" in notice

    def test_subscribe_cta_trimmed(self):
        body, notice = extract_summary(_BLOG_FALLBACK, _BLOG_FALLBACK, None)
        assert "Subscribe now" not in body
        assert "ads/promotions" in notice

    def test_related_content_trimmed(self):
        body, notice = extract_summary(_BLOG_FALLBACK, _BLOG_FALLBACK, None)
        assert "You may also like" not in body
        assert "related content" in notice

    def test_first_and_last_preserved(self):
        """Even in fallback, first and last paragraphs are always kept."""
        body, _ = extract_summary(_BLOG_FALLBACK, _BLOG_FALLBACK, None)
        paragraphs = _split_paragraphs(_BLOG_FALLBACK)
        assert paragraphs[0] in body  # first para always preserved
        assert paragraphs[-1] in body  # last para always preserved

    def test_last_paragraph_percentage_preserved(self):
        """Last paragraph has a percentage and is preserved by both rules."""
        body, _ = extract_summary(_BLOG_FALLBACK, _BLOG_FALLBACK, None)
        assert "35%" in body

    def test_truncation_notice_lists_word_counts(self):
        _, notice = extract_summary(_BLOG_FALLBACK, _BLOG_FALLBACK, None)
        assert "words)" in notice
        assert "word page" in notice


# ---------------------------------------------------------------------------
# Clean academic page (all content has signals)
# ---------------------------------------------------------------------------


class TestCleanAcademicPage:
    """When every paragraph contains signals, nothing should be trimmed."""

    def test_no_content_trimmed(self):
        body, _ = extract_summary(_CLEAN_ACADEMIC, "different raw", None)
        paragraphs = _split_paragraphs(_CLEAN_ACADEMIC)
        for para in paragraphs:
            assert para in body, f"Expected paragraph preserved: {para[:50]}..."

    def test_no_truncation_notice(self):
        _, notice = extract_summary(_CLEAN_ACADEMIC, "different raw", None)
        assert notice == ""


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge cases for smart extraction."""

    def test_empty_content(self):
        body, notice = extract_summary("", "different raw", None)
        assert body == ""
        assert notice == ""

    def test_single_paragraph(self):
        text = "Just one paragraph with some content."
        body, notice = extract_summary(text, "different raw", None)
        # Single paragraph is both first and last -- always preserved
        assert body == text
        assert notice == ""

    def test_title_parameter_accepted(self):
        """Title param is accepted without error (reserved for future use)."""
        body, _ = extract_summary("Some content.", "different raw", "My Title")
        assert body == "Some content."

    def test_paragraph_splitting_double_newline(self):
        text = "First para.\n\nSecond para.\n\nThird para."
        paragraphs = _split_paragraphs(text)
        assert len(paragraphs) == 3

    def test_paragraph_splitting_single_newline_fallback(self):
        text = "Line one.\nLine two.\nLine three."
        paragraphs = _split_paragraphs(text)
        assert len(paragraphs) == 3
