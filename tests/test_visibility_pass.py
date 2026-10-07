"""The Forage-owned visibility pass on the served body (structural-wire-closure US-002).

Inline signals only. ``raw_text`` (stage-3 input), title, author and date always
read the unpruned soup; only ``main_content`` is pruned.

PYTEST_DONT_REWRITE: assertion rewriting is off for this module, so a failing
assert shows only its message, never its operands (which could carry corpus
record text). ``tests/test_corpus_lint.py`` requires this marker in every
corpus test module.
"""

from __future__ import annotations

import time

import pytest
from bs4 import BeautifulSoup

from pipeline.smart_extraction import extract_summary
from pipeline.stage1_extraction import (
    ExtractionResult,
    _parse_style,
    _prune_hidden,
    extract_html,
)
from scripts.corpus.records import load_corpus, page_document
from tests.test_smart_extraction import (
    _BLOG_FALLBACK,
)

_SECRET = "ZZSECRETZZ"
_VISIBLE = "Visible article sentence that the reader sees on the page."


def _page(inner: str, *, head: str = "") -> str:
    return (
        f"<html><head><title>T</title>{head}</head><body>"
        f"<article><p>{_VISIBLE} " * 3 + f"</p>{inner}</article></body></html>"
    )


def _pruned_text(inner: str) -> tuple[str, bool]:
    soup = BeautifulSoup(_page(inner), "lxml")
    pruned, flag = _prune_hidden(soup)
    return pruned.get_text(" "), flag


# ---------------------------------------------------------------------------
# One positive and one negative fixture per rule
# ---------------------------------------------------------------------------

_PRUNED = [
    pytest.param(f"<div hidden>{_SECRET}</div>", id="hidden"),
    pytest.param(f'<div hidden="">{_SECRET}</div>', id="hidden-empty"),
    pytest.param(f'<div aria-hidden="true">{_SECRET}</div>', id="aria-hidden"),
    pytest.param(f'<div style="display:none">{_SECRET}</div>', id="display-none"),
    pytest.param(f'<div style="opacity:0">{_SECRET}</div>', id="opacity-0"),
    pytest.param(f'<div style="opacity: 0.0">{_SECRET}</div>', id="opacity-0.0"),
    pytest.param(f'<div style="opacity:0%">{_SECRET}</div>', id="opacity-0pct"),
    pytest.param(f'<div style="clip:rect(0,0,0,0)">{_SECRET}</div>', id="clip-commas"),
    pytest.param(
        f'<div style="clip: rect(0px 0px 0px 0px)">{_SECRET}</div>', id="clip-spaces"
    ),
    pytest.param(
        f'<div style="clip:rect(0em,0,0pt,0)">{_SECRET}</div>', id="clip-units"
    ),
    pytest.param(f'<div style="text-indent:-9999px">{_SECRET}</div>', id="indent"),
    pytest.param(f'<div style="text-indent:-999px">{_SECRET}</div>', id="indent-edge"),
    pytest.param(
        f'<div style="position:absolute;left:-9999px">{_SECRET}</div>', id="abs-left"
    ),
    pytest.param(
        f'<div style="position:fixed;top:-2000px">{_SECRET}</div>', id="fixed-top"
    ),
    pytest.param(
        f'<div style="overflow:hidden;width:0">{_SECRET}</div>', id="overflow-width"
    ),
    pytest.param(
        f'<div style="overflow:hidden;height:0px">{_SECRET}</div>', id="overflow-height"
    ),
    pytest.param(f'<div style="visibility:hidden">{_SECRET}</div>', id="vis-hidden"),
    pytest.param(
        f'<div style="visibility:collapse">{_SECRET}</div>', id="vis-collapse"
    ),
    pytest.param(f'<div style="font-size:0">{_SECRET}</div>', id="font-0"),
    pytest.param(f'<div style="font-size:0.0em">{_SECRET}</div>', id="font-0em"),
    pytest.param(f'<div style="DISPLAY : None !IMPORTANT">{_SECRET}</div>', id="mixed"),
    pytest.param(
        f'<div style="display:block;display:none">{_SECRET}</div>', id="last-wins-hide"
    ),
    pytest.param(
        f'<div style="color:red;;bogus;display:none">{_SECRET}</div>', id="malformed-ok"
    ),
]

_KEPT = [
    pytest.param(f'<div hidden="until-found">{_SECRET}</div>', id="until-found"),
    pytest.param(f'<div hidden="UNTIL-FOUND">{_SECRET}</div>', id="until-found-case"),
    pytest.param(f'<div aria-hidden="false">{_SECRET}</div>', id="aria-false"),
    pytest.param(f'<div style="display:block">{_SECRET}</div>', id="display-block"),
    pytest.param(f'<div style="opacity:0.5">{_SECRET}</div>', id="opacity-half"),
    pytest.param(f'<div style="opacity:1">{_SECRET}</div>', id="opacity-1"),
    pytest.param(
        f'<div style="clip:rect(0,0,0,1px)">{_SECRET}</div>', id="clip-nonzero"
    ),
    pytest.param(f'<div style="text-indent:-50px">{_SECRET}</div>', id="indent-small"),
    pytest.param(f'<div style="text-indent:-9999em">{_SECRET}</div>', id="indent-em"),
    pytest.param(
        f'<div style="position:absolute;left:-9999em">{_SECRET}</div>', id="offset-em"
    ),
    pytest.param(
        f'<div style="position:relative;left:-9999px">{_SECRET}</div>', id="relative"
    ),
    pytest.param(f'<div style="left:-9999px">{_SECRET}</div>', id="offset-static"),
    pytest.param(f'<div style="overflow:hidden">{_SECRET}</div>', id="overflow-alone"),
    pytest.param(
        f'<div style="overflow:hidden;width:10px;height:10px">{_SECRET}</div>',
        id="overflow-sized",
    ),
    pytest.param(f'<div style="width:0">{_SECRET}</div>', id="width-alone"),
    pytest.param(f'<div style="visibility:visible">{_SECRET}</div>', id="vis-visible"),
    pytest.param(f'<div style="font-size:12px">{_SECRET}</div>', id="font-12"),
    pytest.param(f'<div style="font-size:1px">{_SECRET}</div>', id="font-tiny-kept"),
    pytest.param(
        f'<div style="display:none;display:block">{_SECRET}</div>', id="last-wins-show"
    ),
    pytest.param(f'<div style="display none">{_SECRET}</div>', id="malformed-kept"),
]


@pytest.mark.parametrize("inner", _PRUNED)
def test_a_listed_signal_prunes_the_subtree(inner: str) -> None:
    text, pruned = _pruned_text(inner)
    assert pruned
    assert _SECRET not in text
    assert _VISIBLE in text


@pytest.mark.parametrize("inner", _KEPT)
def test_an_unlisted_signal_keeps_the_text(inner: str) -> None:
    text, pruned = _pruned_text(inner)
    assert _SECRET in text
    assert not pruned


def test_a_descendant_of_a_pruned_subtree_goes_with_it() -> None:
    text, pruned = _pruned_text(
        f'<div style="display:none"><p style="display:block">{_SECRET}</p>'
        f'<span style="visibility:visible">{_SECRET}</span></div>'
    )
    assert pruned
    assert _SECRET not in text


# ---------------------------------------------------------------------------
# Inherited signals and re-shows
# ---------------------------------------------------------------------------


def test_visibility_hidden_removes_own_text_but_keeps_a_reshown_descendant() -> None:
    text, pruned = _pruned_text(
        f'<div style="visibility:hidden">{_SECRET}'
        f'<p style="visibility:visible">SHOWN</p><p>{_SECRET}-2</p></div>'
    )
    assert pruned
    assert "SHOWN" in text
    assert _SECRET not in text


def test_a_reshown_descendant_keeps_its_own_subtree() -> None:
    text, _ = _pruned_text(
        '<div style="visibility:hidden"><div style="visibility:visible">'
        "<p>deep <b>kept</b></p></div></div>"
    )
    assert "deep kept" in text.replace("  ", " ")


@pytest.mark.parametrize(
    "unit", ["12px", "10pt", "1pc", "1cm", "5mm", "1in", "9Q", "1rem"]
)
def test_an_absolute_or_root_relative_font_size_reshows_under_a_zero_parent(
    unit: str,
) -> None:
    text, pruned = _pruned_text(
        f'<div style="font-size:0">{_SECRET}<p style="font-size:{unit}">SHOWN</p></div>'
    )
    assert pruned
    assert "SHOWN" in text
    assert _SECRET not in text


@pytest.mark.parametrize(
    "size", ["1em", "150%", "2ex", "1ch", "larger", "inherit", "x"]
)
def test_a_relative_font_size_under_a_zero_parent_is_not_a_reshow(size: str) -> None:
    text, pruned = _pruned_text(
        f'<div style="font-size:0"><p style="font-size:{size}">{_SECRET}</p></div>'
    )
    assert pruned
    assert _SECRET not in text


def test_visibility_visible_does_not_undo_a_zero_font_size() -> None:
    text, _ = _pruned_text(
        f'<div style="font-size:0"><p style="visibility:visible">{_SECRET}</p></div>'
    )
    assert _SECRET not in text


def test_a_reshown_font_size_does_not_undo_hidden_visibility() -> None:
    text, _ = _pruned_text(
        f'<div style="visibility:hidden"><p style="font-size:12px">{_SECRET}</p></div>'
    )
    assert _SECRET not in text


def test_a_nonoverridable_signal_beats_a_reshow_on_the_same_element() -> None:
    text, _ = _pruned_text(
        f'<div style="display:none;visibility:visible;font-size:12px">{_SECRET}</div>'
    )
    assert _SECRET not in text


# ---------------------------------------------------------------------------
# Scope: body descendants only
# ---------------------------------------------------------------------------


def test_head_title_meta_and_html_body_attributes_are_never_pruned() -> None:
    page = (
        '<html hidden style="display:none"><head><title>Kept title</title>'
        '<meta name="author" content="Ann" hidden></head>'
        '<body hidden style="display:none" aria-hidden="true">'
        f"<p>{_VISIBLE * 3}</p></body></html>"
    )
    result = extract_html(page)
    assert result.title == "Kept title"
    assert result.author == "Ann"
    assert "Visible article" in result.main_content
    _, pruned = _prune_hidden(BeautifulSoup(page, "lxml"))
    assert not pruned


# ---------------------------------------------------------------------------
# Style parsing
# ---------------------------------------------------------------------------


def test_style_parsing_is_case_whitespace_and_important_tolerant() -> None:
    parsed = _parse_style("  DISPLAY :  None  !Important ; Color:RED;;bogus;:x;a:b:c")
    assert parsed["display"] == "none"
    assert parsed["color"] == "red"
    assert parsed["a"] == "b:c"
    assert "bogus" not in parsed
    assert "" not in parsed


def test_style_parsing_last_declaration_wins() -> None:
    assert _parse_style("display:none;display:block")["display"] == "block"


@pytest.mark.parametrize(
    "style",
    [
        "display",
        ";;;",
        ":",
        "display:",
        "opacity:abc",
        "opacity:0.0.0",
        "clip:rect(",
        "left:-.px",
    ],
)
def test_malformed_styles_keep_the_element(style: str) -> None:
    text, pruned = _pruned_text(f'<div style="{style}">{_SECRET}</div>')
    assert _SECRET in text
    assert not pruned


def test_deep_nesting_and_a_huge_style_value_are_processed_without_raising() -> None:
    depth = 10_000
    page = (
        "<html><body>"
        + '<div style="color:red">' * depth
        + f"<p>{_VISIBLE}</p>"
        + "</div>" * depth
        + f'<div style="{"a:b;" * 200_000}display:none">{_SECRET}</div>'
        + "</body></html>"
    )
    start = time.perf_counter()
    result = extract_html(page)
    elapsed = time.perf_counter() - start
    assert elapsed < 30
    assert isinstance(result, ExtractionResult)
    huge = "x" * 1_000_000
    big = f'<html><body><p>{_VISIBLE}</p><div style="{huge}">{_SECRET}</div></body>'
    start = time.perf_counter()
    extract_html(big)
    assert time.perf_counter() - start < 30


def test_a_huge_style_value_with_a_trailing_signal_is_still_pruned() -> None:
    page = _page(f'<div style="{"a:b;" * 100_000}display:none">{_SECRET}</div>')
    assert _SECRET not in extract_html(page).main_content


# ---------------------------------------------------------------------------
# Never mutates the shared soup; raw_text and metadata unchanged
# ---------------------------------------------------------------------------


def test_the_prune_helper_never_mutates_the_shared_soup() -> None:
    soup = BeautifulSoup(_page(f"<div hidden>{_SECRET}</div>"), "lxml")
    before = str(soup)
    pruned, flag = _prune_hidden(soup)
    assert flag
    assert str(soup) == before
    assert pruned is not soup


def test_raw_text_and_metadata_are_identical_with_pruning_on_and_off() -> None:
    page = (
        "<html><head><title>Real title</title>"
        '<meta name="author" content="Ann"><meta name="date" content="2026-01-02">'
        f"</head><body><p>{_VISIBLE * 5}</p>"
        f'<div style="display:none">{_SECRET}</div>'
        f'<div style="font-size:0">{_SECRET}2</div></body></html>'
    )
    on = extract_html(page, with_inline=True)
    off = extract_html(page, with_inline=True, prune_hidden=False)
    assert (on.raw_text, on.title, on.author, on.date, on.scan_text_inline) == (
        off.raw_text,
        off.title,
        off.author,
        off.date,
        off.scan_text_inline,
    )
    assert _SECRET in on.raw_text
    assert _SECRET in (on.scan_text_inline or "")
    assert _SECRET not in on.main_content
    assert (
        _SECRET in off.main_content or _SECRET not in off.main_content
    )  # unpruned path


def test_corpus_raw_text_is_identical_and_benign_bodies_do_not_move() -> None:
    pages = [r for r in load_corpus() if r.surface == "page"]
    assert len(pages) > 300
    moved_benign: list[str] = []
    for record in pages:
        document = page_document(record)
        url = record.payload.get("url")
        on = extract_html(document, url)
        off = extract_html(document, url, prune_hidden=False)
        assert on.raw_text == off.raw_text, record.id
        assert (on.title, on.author, on.date) == (off.title, off.author, off.date)
        if on.main_content != off.main_content:
            assert on.main_content_is_fallback is not None, record.id
            if record.id.startswith("ben-"):
                moved_benign.append(record.id)
    assert moved_benign == []


def test_the_hidden_markup_attack_carriers_are_pruned_from_the_body() -> None:
    carriers = [
        r
        for r in load_corpus()
        if r.surface == "page"
        and r.params.get("carrier") in ("css_offscreen", "hidden_div")
    ]
    assert len(carriers) >= 6
    for record in carriers:
        assert record.marker is not None
        result = extract_html(page_document(record), record.payload.get("url"))
        assert record.marker not in result.main_content, record.id
        assert record.marker in result.raw_text, record.id


# ---------------------------------------------------------------------------
# The fallback flag
# ---------------------------------------------------------------------------


def test_the_flag_is_none_on_an_unpruned_page_including_a_short_equal_one() -> None:
    short = "<html><body><p>Just a short page.</p></body></html>"
    result = extract_html(short)
    assert result.main_content_is_fallback is None
    # The inferred branch is unchanged: equality decides, exactly as before.
    expected = extract_summary(result.main_content, result.raw_text, result.title)
    assert (
        extract_summary(
            result.main_content,
            result.raw_text,
            result.title,
            result.main_content_is_fallback,
        )
        == expected
    )
    assert extract_html(short, prune_hidden=False).main_content_is_fallback is None


def test_the_flag_is_false_when_trafilatura_served_a_pruned_page() -> None:
    result = extract_html(_page(f"<div hidden>{_SECRET}</div>"))
    assert result.main_content_is_fallback is False
    assert _SECRET not in result.main_content
    assert _SECRET in result.raw_text


def test_the_flag_is_true_on_the_pruned_fallback_path() -> None:
    page = f"<html><body><p>hi</p><div hidden>{_SECRET}</div><p>there</p></body></html>"
    from unittest.mock import patch

    with patch("pipeline.stage1_extraction._extract_main_content", return_value=None):
        result = extract_html(page)
    assert result.main_content_is_fallback is True
    assert result.main_content == "hi\nthere"
    assert _SECRET not in result.main_content


def test_extract_summary_trusts_the_flag_and_infers_only_when_none() -> None:
    text = _BLOG_FALLBACK
    fallback_branch = extract_summary(text, text, None)
    trafilatura_branch = extract_summary(text, "different raw", None)
    assert fallback_branch != trafilatura_branch
    # An explicit flag overrides the equality test in both directions.
    assert extract_summary(text, "different raw", None, True) == fallback_branch
    assert extract_summary(text, text, None, False) == trafilatura_branch
    # None infers by equality, which is today's behaviour.
    assert extract_summary(text, text, None, None) == fallback_branch
    assert extract_summary(text, "different raw", None, None) == trafilatura_branch


def test_summary_mode_on_a_pruned_fallback_page_is_defined() -> None:
    result = ExtractionResult(
        title=None,
        author=None,
        date=None,
        raw_text="raw " + _SECRET,
        main_content="Visible fallback text only. " * 40,
        word_count=200,
        main_content_is_fallback=True,
    )
    body, _ = extract_summary(
        result.main_content, result.raw_text, None, result.main_content_is_fallback
    )
    assert _SECRET not in body
    assert body


# ---------------------------------------------------------------------------
# Benign-loss decisions (documented costs)
# ---------------------------------------------------------------------------

_PARAGRAPH = (
    "The harbour committee met on Tuesday evening to discuss the long-running "
    "dredging plan, the budget for the new pier and the schedule of works. "
)


def _article(extra: str) -> str:
    return (
        "<html><head><title>Harbour</title></head><body><article>"
        f"<h1>Harbour plan</h1><p>{_PARAGRAPH * 6}</p><p>{_PARAGRAPH * 4}</p>"
        f"{extra}</article></body></html>"
    )


def test_an_aria_tab_panel_using_hidden_is_pruned() -> None:
    body = extract_html(
        _article(f'<div role="tabpanel" hidden><p>TABPANEL {_PARAGRAPH}</p></div>')
    ).main_content
    assert "TABPANEL" not in body
    assert "dredging plan" in body


def test_a_hidden_until_found_section_is_kept() -> None:
    body = extract_html(
        _article(
            f'<section hidden="until-found"><p>FINDABLE {_PARAGRAPH * 3}</p></section>'
        )
    ).main_content
    assert "FINDABLE" in body


def test_an_opacity_zero_animated_hero_is_pruned_as_a_documented_cost() -> None:
    body = extract_html(
        _article(f'<div style="opacity:0"><p>HERO {_PARAGRAPH * 3}</p></div>')
    ).main_content
    assert "HERO" not in body


def test_a_font_size_zero_layout_container_with_reshown_children_is_kept() -> None:
    body = extract_html(
        _article(
            '<div style="font-size:0">'
            f'<p style="font-size:16px">CHILD {_PARAGRAPH * 3}</p></div>'
        )
    ).main_content
    assert "CHILD" in body


def test_pruning_the_element_trafilatura_would_pick_serves_the_visible_remainder() -> (
    None
):
    page = (
        "<html><head><title>T</title></head><body>"
        f'<div id="main" hidden><article><p>BIG {_PARAGRAPH * 40}</p></article></div>'
        f"<p>{_PARAGRAPH}</p></body></html>"
    )
    result = extract_html(page)
    assert "BIG" not in result.main_content
    assert "dredging plan" in result.main_content
    assert result.main_content_is_fallback is not None
