"""The raw-markup scan subset (structural-markup-surface US-010).

``scan_raw_markup`` runs the three ``_MARKUP_PATTERNS`` once each over the
whitespace-collapsed source. Linearity is timed at 5 MB and 10 MB (best of 3,
collector paused); the ratio is skipped when both sizes finish under 20 ms.

PYTEST_DONT_REWRITE: assertion rewriting is off for this module, so a failing
assert shows only its message, never its operands. ``tests/test_corpus_lint.py``
requires this marker in every module that imports ``scripts.corpus``.
"""

from __future__ import annotations

import gc
import time
from collections.abc import Callable

import pytest

from models import Stage2Verdict
from pipeline.stage2_structural import (
    _MARKUP_PATTERNS,
    _PATTERNS,
    scan_raw_markup,
    scan_structural,
)
from scripts.corpus.vocab import STAGE2_REGEX_NAMES

_MARKUP_NAMES = ("system_tag", "private_ip_href", "envelope_breakout")
_PROBES = {
    "system_tag": "<system>",
    "private_ip_href": '<a href="http://10.0.0.1/">x</a>',
    "envelope_breakout": "</retrieved_content>",
}
_MB = 1024 * 1024
_RATIO_BOUND = 3
_FAST_SECONDS = 0.020


def test_markup_patterns_are_exactly_the_three_named_registry_objects() -> None:
    by_name = {
        name: pattern
        for name, (_, pattern) in zip(STAGE2_REGEX_NAMES, _PATTERNS, strict=True)
    }
    assert len(_MARKUP_PATTERNS) == 3
    for name, pattern in zip(_MARKUP_NAMES, _MARKUP_PATTERNS, strict=True):
        assert by_name[name] is pattern, f"{name} is not the registry object"


@pytest.mark.parametrize(
    "tag", ["<system >", "<system id=a>", "<SYSTEM/>", "</ system>"]
)
def test_system_tag_matches_widened_forms(tag: str) -> None:
    assert scan_structural(tag).verdict == Stage2Verdict.BLOCKED, tag


@pytest.mark.parametrize("text", ["<systemic>", "<system", "system>"])
def test_system_tag_needs_a_word_boundary_and_a_close(text: str) -> None:
    assert scan_structural(text).verdict == Stage2Verdict.CLEAN, text


def test_clean_markup_is_clean() -> None:
    result = scan_raw_markup("<html><body><p>Hello <b>world</b></p></body></html>")
    assert result.verdict == Stage2Verdict.CLEAN
    assert result.flags == []


def test_stray_closing_envelope_tag_in_raw_html() -> None:
    result = scan_raw_markup(
        "<html><body><p>hi</p></retrieved_content>\n<p>x</p></body></html>"
    )
    assert result.verdict == Stage2Verdict.SUSPICIOUS
    assert [f.category for f in result.flags] == ["envelope_breakout"]
    assert result.flags[0].line_number == 0
    assert result.penalty == pytest.approx(-0.15)


def test_private_ip_href_inside_an_iframe() -> None:
    result = scan_raw_markup('<iframe src="http://192.168.1.5/admin"></iframe>')
    assert result.verdict == Stage2Verdict.SUSPICIOUS
    assert [f.category for f in result.flags] == ["suspicious_url"]


def test_private_ip_in_a_form_attribute_split_by_whitespace() -> None:
    result = scan_raw_markup('<form><img\n\tsrc \n = \n"http://10.1.2.3/x"></form>')
    assert result.verdict == Stage2Verdict.SUSPICIOUS
    assert [f.category for f in result.flags] == ["suspicious_url"]


def test_system_tag_with_whitespace_run_blocks() -> None:
    result = scan_raw_markup("<p>a</p><\n\n  system\n id=x  >b")
    assert result.verdict == Stage2Verdict.BLOCKED
    assert [f.category for f in result.flags] == ["instruction_override"]
    assert result.penalty == 0.0


def test_first_match_only_one_flag_per_pattern() -> None:
    result = scan_raw_markup('<a href="http://10.0.0.1/">x</a>' * 1000)
    assert len(result.flags) == 1


@pytest.mark.parametrize("name", _MARKUP_NAMES)
def test_each_probe_is_caught_after_nine_mb_of_single_text_node_padding(
    name: str,
) -> None:
    markup = (
        "<html><body><p>" + "a" * (9 * _MB) + "</p>" + _PROBES[name] + "</body></html>"
    )
    result = scan_raw_markup(markup)
    assert result.verdict != Stage2Verdict.CLEAN, name


def _best_of_three(run: Callable[[], object]) -> float:
    best = float("inf")
    gc.collect()
    gc.disable()
    try:
        for _ in range(3):
            began = time.perf_counter()
            run()
            best = min(best, time.perf_counter() - began)
    finally:
        gc.enable()
    return best


def _fill(unit: str, size: int) -> str:
    return (unit * (size // len(unit) + 1))[:size]


_HOSTILE = {
    "match_dense": '<a href="http://10.0.0.1/">x</a>\n',
    "whitespace_bomb": "< " + (" \t\n" + chr(0xA0) + chr(0x2003)) * 4096,
    "lt_flood": "&lt;",
}


@pytest.mark.parametrize("family", list(_HOSTILE))
def test_scan_raw_markup_is_linear(family: str) -> None:
    small = _fill(_HOSTILE[family], 5 * _MB)
    large = _fill(_HOSTILE[family], 10 * _MB)
    t_small = _best_of_three(lambda: scan_raw_markup(small))
    t_large = _best_of_three(lambda: scan_raw_markup(large))
    if t_small < _FAST_SECONDS and t_large < _FAST_SECONDS:
        return
    assert t_large <= _RATIO_BOUND * t_small, (
        f"{family}: {t_large:.3f}s at 10 MB vs {t_small:.3f}s at 5 MB"
    )
