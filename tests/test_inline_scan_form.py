"""The inline-joined scan form (structural-markup-surface US-001).

``extract_html(with_inline=True)`` builds, from its own soup and in one
iterative walk, the page text with every non-block element joined into its
surroundings. Stage 2 scans it as an extra form; ``raw_text`` is untouched.

PYTEST_DONT_REWRITE: assertion rewriting is off for this module, so a failing
assert shows only its message, never its operands (which could carry record
text). ``tests/test_corpus_lint.py`` requires this marker in every corpus test
module.
"""

from __future__ import annotations

import html
import time
from typing import Any
from unittest.mock import patch

import pytest
from bs4 import BeautifulSoup

from models import Stage2Verdict
from pipeline import orchestrator
from pipeline.html_subprocess import extract_html_and_scan
from pipeline.stage1_extraction import _extract_inline_text, extract_html
from pipeline.stage2_structural import (
    scan_structural,
    scan_structural_forms,
    structural_scan_forms,
)
from scripts.corpus import vocab
from scripts.corpus.records import load_corpus, page_document

# (prefix, suffix) wrapped around the second half; "" prefix means a bare insert.
_SPLITTERS: tuple[tuple[str, str], ...] = (
    ("<b></b>", ""),
    ("<span>", "</span>"),
    ("<wbr>", ""),
    ("<font>", "</font>"),
    ("<x-custom>", "</x-custom>"),
)


def _inline(markup: str) -> str:
    return extract_html(markup, with_inline=True).scan_text_inline or ""


def _splits(probe: str, *, escape: Any) -> list[str]:
    """Split between probe characters (unescaped), then escape each half."""
    pages: list[str] = []
    for index in range(1, len(probe)):
        first, second = escape(probe[:index]), escape(probe[index:])
        for prefix, suffix in _SPLITTERS:
            pages.append(f"{first}{prefix}{second}{suffix}")
    return pages


def _retrieve_verdict(body: str) -> Stage2Verdict:
    page = f"<html><body><p>{body}</p></body></html>"
    extraction, inline_scan = extract_html_and_scan(page, None, None)
    as_is = scan_structural_forms(
        structural_scan_forms(extraction.raw_text, html_parsed=True)
    )
    assert inline_scan is not None
    return max(
        (as_is.verdict, inline_scan.verdict),
        key=lambda v: (
            Stage2Verdict.CLEAN,
            Stage2Verdict.SUSPICIOUS,
            Stage2Verdict.BLOCKED,
        ).index(v),
    )


def _search_verdict(body: str) -> Stage2Verdict:
    wire, scan, inline = orchestrator._scan_forms_for_search_text(
        body, max_length=2_000
    )
    verdicts = [
        scan_structural(text).verdict
        for text in (scan, wire, *structural_scan_forms(inline, html_parsed=True))
    ]
    for rank in (Stage2Verdict.BLOCKED, Stage2Verdict.SUSPICIOUS):
        if rank in verdicts:
            return rank
    return Stage2Verdict.CLEAN


# ---------------------------------------------------------------------------
# The walker
# ---------------------------------------------------------------------------


def test_a_non_block_element_is_joined_into_its_surrounding_text() -> None:
    assert "abcd" in _inline("<p>ab<b></b>cd</p>")


def test_block_elements_keep_their_text_on_separate_lines() -> None:
    lines = _inline("<p>ab</p><p>cd</p>").splitlines()
    assert "ab" in lines and "cd" in lines
    assert "abcd" not in _inline("<p>ab</p><p>cd</p>")


@pytest.mark.parametrize("tag", ["wbr", "font", "span", "x-custom", "i"])
def test_unlisted_elements_are_joined(tag: str) -> None:
    assert "abcd" in _inline(f"<p>ab<{tag}>c</{tag}>d</p>")


@pytest.mark.parametrize("tag", ["div", "li", "td", "h1", "br", "section"])
def test_listed_block_elements_separate(tag: str) -> None:
    assert "abcd" not in _inline(f"<body>ab<{tag}>c</{tag}>d</body>")


def test_dangerous_subtrees_and_comments_are_skipped() -> None:
    text = _inline(
        "<p>a<script>BAD</script><style>BAD</style><!--BAD--><b>b</b>"
        "<form>BAD</form><svg>BAD</svg></p>"
    )
    assert "BAD" not in text
    assert "ab" in text


def test_the_inline_form_is_off_by_default() -> None:
    assert extract_html("<p>x</p>").scan_text_inline is None


def test_with_inline_does_not_change_raw_text_or_main_content() -> None:
    page = "<html><body><p>ab<b></b>cd</p><p>ef</p></body></html>"
    plain = extract_html(page)
    inline = extract_html(page, with_inline=True)
    assert inline.raw_text == plain.raw_text
    assert inline.main_content == plain.main_content
    assert inline.title == plain.title


def _sibling_walk_time(count: int) -> float:
    soup = BeautifulSoup("<p>" + "<b>x</b>" * count + "</p>", "lxml")
    best = float("inf")
    for _ in range(3):
        start = time.perf_counter()
        _extract_inline_text(soup)
        best = min(best, time.perf_counter() - start)
    return best


def test_building_the_inline_form_is_linear_in_sibling_elements() -> None:
    small, large = _sibling_walk_time(40_000), _sibling_walk_time(80_000)
    assert large <= 3 * max(small, 1e-4)


# ---------------------------------------------------------------------------
# The corpus
# ---------------------------------------------------------------------------


def _page_records() -> list[Any]:
    return [r for r in load_corpus() if r.surface == "page"]


def test_raw_text_is_unchanged_and_the_inline_form_has_its_characters() -> None:
    records = _page_records()
    assert len(records) > 100
    for record in records:
        document = page_document(record)
        plain = extract_html(document, record.payload.get("url"))
        inline = extract_html(document, record.payload.get("url"), with_inline=True)
        assert inline.raw_text == plain.raw_text, record.id
        assert inline.scan_text_inline is not None, record.id
        assert "".join(inline.scan_text_inline.split()) == "".join(
            plain.raw_text.split()
        ), record.id


# ---------------------------------------------------------------------------
# Class-level property: any split by a non-block element is caught
# ---------------------------------------------------------------------------


def _hex_double(text: str) -> str:
    return "".join(f"&amp;#x{ord(ch):x};" for ch in text)


@pytest.mark.parametrize("name", sorted(vocab.STAGE2_REGEX_PROBES))
def test_a_split_probe_is_caught_on_retrieve(name: str) -> None:
    probe = vocab.STAGE2_REGEX_PROBES[name]
    for body in _splits(probe, escape=html.escape):
        assert _retrieve_verdict(body) != Stage2Verdict.CLEAN, (name, body)


@pytest.mark.parametrize("name", sorted(vocab.STAGE2_REGEX_PROBES))
def test_a_split_probe_is_caught_on_search(name: str) -> None:
    probe = vocab.STAGE2_REGEX_PROBES[name]
    for body in _splits(probe, escape=html.escape):
        assert _search_verdict(body) != Stage2Verdict.CLEAN, (name, body)


@pytest.mark.parametrize("name", sorted(vocab.STAGE2_REGEX_PROBES))
def test_a_split_double_entity_encoded_probe_is_caught_on_search(name: str) -> None:
    probe = vocab.STAGE2_REGEX_PROBES[name]
    for body in _splits(probe, escape=_hex_double):
        assert _search_verdict(body) != Stage2Verdict.CLEAN, (name, body)


# ---------------------------------------------------------------------------
# One parse, and only a result leaves the thread
# ---------------------------------------------------------------------------


def _count_parses() -> Any:
    """Count parser runs. The visibility pass re-parses its input too."""
    return patch.object(
        BeautifulSoup, "_feed", autospec=True, side_effect=BeautifulSoup._feed
    )


def test_retrieve_parses_once_and_only_a_scan_result_leaves_the_thread() -> None:
    page = "<html><body><p>ig<b></b>nore previous</p></body></html>"
    with _count_parses() as baseline:
        extract_html(page)
    with _count_parses() as feed:
        extraction, inline_scan = extract_html_and_scan(page, None, None)
    # The inline form adds no parse of its own: same parser runs as without it.
    assert feed.call_count == baseline.call_count
    assert extraction.scan_text_inline is None
    assert inline_scan is not None
    assert inline_scan.verdict == Stage2Verdict.BLOCKED


def test_an_over_budget_page_skips_the_inline_scan() -> None:
    extraction, inline_scan = extract_html_and_scan(
        "<p>" + "word " * 50 + "</p>", None, 10
    )
    assert extraction.scan_text_inline is None
    assert inline_scan is None


def test_search_parses_each_field_once() -> None:
    field = "ig<b></b>nore previous"
    with _count_parses() as baseline:
        extract_html(f"<div>{field}</div>")
    with _count_parses() as feed:
        orchestrator._scan_forms_for_search_text(field, max_length=200)
    assert feed.call_count == baseline.call_count


def test_the_search_wire_form_is_unchanged_by_the_inline_form() -> None:
    wire, scan, inline = orchestrator._scan_forms_for_search_text(
        "<p>ab</p><b>c</b>d", max_length=200
    )
    assert wire == " ".join(scan.split())
    assert "bcd" not in inline and "cd" in inline


# ---------------------------------------------------------------------------
# Accepted-cost benign fixtures (the corpus has no code-genre page records)
# ---------------------------------------------------------------------------


def test_a_bold_role_label_at_line_start_is_an_accepted_cost() -> None:
    assert _retrieve_verdict("<b>Assistant</b>: here you go") == Stage2Verdict.BLOCKED


def test_syntax_highlighted_code_rejoining_into_a_base64_run_is_flagged() -> None:
    runs = ["QUJDREVGR0hJSktM", "TU5PUFFSU1RVVldY", "WVphYmNkZWZn"]
    code = "".join(f"<span>{run}</span>" for run in runs)
    page = f"<pre><code>{code}</code></pre>"
    extraction = extract_html(page, with_inline=True)
    assert scan_structural(extraction.raw_text).verdict == Stage2Verdict.CLEAN
    assert _retrieve_verdict(code) == Stage2Verdict.SUSPICIOUS
