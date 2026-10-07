"""Stage 1 is linear on hostile input (``release-resource-bounds`` US-001).

Each pinned shape is timed at 64 KiB and 256 KiB (GC off, best of 3) through
``extract_html(..., with_inline=True)`` plus the visibility pass. Linear scaling
is 4x, quadratic 16x, so the ratio bound is 8x; a pair that finishes the large
size under the fast floor proves itself. Ceilings are absolute guards against a
constant-factor regression.

The equivalence tests compare every ``ExtractionResult`` field, as a digest,
with values frozen before the soup copies were removed, so no corpus text is
read or committed here.

PYTEST_DONT_REWRITE: assertion rewriting is off for this module, so a failing
assert shows only its message. ``tests/test_corpus_lint.py`` requires this
marker in every corpus test module.
"""

from __future__ import annotations

import gc
import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from pipeline.stage1_extraction import extract_html
from tests.stage1_equivalence import (
    COMBINATIONS,
    SYNTHETIC_PAGES,
    digests,
)
from tests.stage1_shapes import SHAPES

_SMALL = 64 * 1024
_LARGE = 256 * 1024
_RATIO_BOUND = 8
_FAST_SECONDS = 0.100
_CEILING_SECONDS = 5.0
_FROZEN = Path(__file__).parent / "golden" / "stage1_equivalence.json"


def _best_of_three(html: str) -> float:
    best = float("inf")
    gc.disable()
    try:
        for _ in range(3):
            start = time.perf_counter()
            extract_html(html, with_inline=True, prune_hidden=True)
            best = min(best, time.perf_counter() - start)
    finally:
        gc.enable()
    return best


@pytest.mark.parametrize("name", sorted(SHAPES))
def test_each_hostile_shape_scales_linearly(name: str) -> None:
    t_small = _best_of_three(SHAPES[name](_SMALL))
    t_large = _best_of_three(SHAPES[name](_LARGE))
    assert t_large < _CEILING_SECONDS, name
    assert t_large <= _FAST_SECONDS or t_large <= _RATIO_BOUND * t_small, name


def test_every_page_matches_the_pre_story_digest() -> None:
    frozen: dict[str, str] = json.loads(_FROZEN.read_text())
    current = digests(extract_html)
    assert current.keys() == frozen.keys()
    changed = sorted(
        key.split("|")[0] for key in current if current[key] != frozen[key]
    )
    assert not changed, sorted(set(changed))


def test_the_pruned_fallback_keeps_hidden_text_out() -> None:
    page = SYNTHETIC_PAGES["pruned_fallback"]
    for inline, prune in COMBINATIONS:
        with patch(
            "pipeline.stage1_extraction._extract_main_content", return_value=None
        ):
            result = extract_html(page, with_inline=inline, prune_hidden=prune)
        assert "HIDDENTEXT" in result.raw_text
        if prune:
            assert result.main_content_is_fallback is True
            assert "HIDDENTEXT" not in result.main_content
            assert "one" in result.main_content
