"""The calibrated ``html_worker_threshold_bytes`` default still holds (US-006).

The default is the largest power-of-two KiB at which the worst pinned stage-1
shape parses in-thread in <= 2 s and the parent's peak-RSS delta stays <= 25% of
the 1536 MiB Compose default (``docs/configuration.md``, "HTML worker
threshold"). This module guards the time axis: every pinned shape is timed
through the in-thread path (``extract_html_and_scan``, GC off, best of 3) at a
quarter of the default and at the default itself. Linear scaling over a 4x
size step is 4x, quadratic 16x, so the blocking assertion is the ratio bound,
with the fast-floor escape ``tests/test_stage2_complexity.py`` uses. The
absolute ceilings are a generous 5x of the seconds recorded on the dev machine
(macOS arm64, median of 3, 512 KiB, parent peak-RSS delta in MiB beside):

    sibling_dense         0.88 s   156 MiB
    deep_span             0.29 s    48 MiB
    attribute_heavy       0.35 s    76 MiB
    unclosed_span         0.68 s    75 MiB
    deep_span_hidden      0.48 s    66 MiB
    unclosed_span_hidden  1.19 s   149 MiB   (the worst shape)

The RSS axis is recorded, not asserted: a peak needs a fresh process per sample.
"""

from __future__ import annotations

import gc
import time

import pytest

from pipeline.html_subprocess import extract_html_and_scan
from pipeline.retrieve_limits import RETRIEVE_HTML_WORKER_THRESHOLD_BYTES
from tests.stage1_shapes import SHAPES

_LARGE = RETRIEVE_HTML_WORKER_THRESHOLD_BYTES
_SMALL = _LARGE // 4
_RATIO_BOUND = 8
_FAST_SECONDS = 0.100
_CEILING_MULTIPLE = 5
_RECORDED_SECONDS = {
    "sibling_dense": 0.88,
    "deep_span": 0.29,
    "attribute_heavy": 0.35,
    "unclosed_span": 0.68,
    "deep_span_hidden": 0.48,
    "unclosed_span_hidden": 1.19,
}
# The two-axis limit the default was chosen against.
_IN_THREAD_SECONDS_LIMIT = 2.0


def _best_of_three(html: str) -> float:
    best = float("inf")
    gc.disable()
    try:
        for _ in range(3):
            start = time.perf_counter()
            extract_html_and_scan(html, "https://example.com/", None)
            best = min(best, time.perf_counter() - start)
    finally:
        gc.enable()
    return best


def test_the_recorded_seconds_cover_every_pinned_shape() -> None:
    assert set(_RECORDED_SECONDS) == set(SHAPES)
    assert max(_RECORDED_SECONDS.values()) <= _IN_THREAD_SECONDS_LIMIT


@pytest.mark.parametrize("name", sorted(SHAPES))
def test_each_pinned_shape_stays_linear_and_inside_its_ceiling_at_the_default(
    name: str,
) -> None:
    t_small = _best_of_three(SHAPES[name](_SMALL))
    t_large = _best_of_three(SHAPES[name](_LARGE))
    assert t_large <= _CEILING_MULTIPLE * _RECORDED_SECONDS[name], (
        f"{name}: {t_large:.3f}s at the default threshold exceeds "
        f"{_CEILING_MULTIPLE}x its recorded {_RECORDED_SECONDS[name]}s"
    )
    assert t_large < _FAST_SECONDS or t_large <= _RATIO_BOUND * t_small, (
        f"{name}: {t_large:.3f}s at {_LARGE} bytes vs {t_small:.3f}s at {_SMALL}"
    )
