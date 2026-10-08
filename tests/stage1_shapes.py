"""Pinned hostile stage-1 shapes, built from string multiplication only.

Shared by ``tests/test_stage1_complexity.py`` and the stage-1 calibration
(``release-resource-bounds`` US-006). No corpus text: every shape is a repeated
synthetic fragment sized to a target byte count.
"""

from __future__ import annotations

from collections.abc import Callable

_HIDDEN = "<div hidden>secret</div>"


def _wrap(inner: str) -> str:
    return f"<html><head><title>T</title></head><body>{inner}</body></html>"


def _repeat(unit: str, size: int) -> str:
    return unit * max(1, size // len(unit))


def sibling_dense(size: int) -> str:
    """Many flat siblings: ``<b>g</b>`` repeated."""
    return _wrap(_repeat("<b>g</b>", size))


def deep_span(size: int) -> str:
    """Open N spans, text, close N spans."""
    depth = max(1, (size - 20) // 14)
    return _wrap("<span>" * depth + "x" + "</span>" * depth)


def attribute_heavy(size: int) -> str:
    """Elements carrying long attribute and style lists."""
    unit = (
        '<p class="a b c d" id="n" style="color:red;margin:0;padding:0" '
        'data-k="v">t</p>'
    )
    return _wrap(_repeat(unit, size))


def unclosed_span(size: int) -> str:
    """Repeated unclosed ``<span>x`` with no closing tags."""
    return _wrap(_repeat("<span>x", size))


def deep_span_hidden(size: int) -> str:
    """The deep shape with a hidden element, so the visibility pass runs."""
    depth = max(1, (size - 60) // 14)
    return _wrap(_HIDDEN + "<span>" * depth + "x" + "</span>" * depth)


def unclosed_span_hidden(size: int) -> str:
    """The unclosed shape with a hidden element, so the visibility pass runs."""
    return _wrap(_HIDDEN + _repeat("<span>x", size))


SHAPES: dict[str, Callable[[int], str]] = {
    "sibling_dense": sibling_dense,
    "deep_span": deep_span,
    "attribute_heavy": attribute_heavy,
    "unclosed_span": unclosed_span,
    "deep_span_hidden": deep_span_hidden,
    "unclosed_span_hidden": unclosed_span_hidden,
}
