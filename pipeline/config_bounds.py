"""Bounded config readers shared by the ``pipeline/`` settings modules.

Two private ``_bounded_int`` copies existed before this module — one in
``pipeline/extraction_limits.py`` and one in ``cache.py`` — and
``pipeline/retrieve_limits.py`` needed a third plus a float variant. The
``extraction_limits`` copy is migrated onto these helpers; ``cache.py``'s
stays where it is, for the reason recorded beside it (``cache.py`` is
*imported by* ``pipeline/orchestrator.py``, so importing back out of
``pipeline`` would close a dependency loop for twelve lines). Two
implementations, each with a written reason, rather than three.

The exception class is a parameter because each caller raises its own
``ValueError`` subclass: a boot refusal names the block whose key was wrong.
"""

from __future__ import annotations

from typing import Any


def bounded_int(
    config: dict[str, Any],
    key: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
    error: type[ValueError],
) -> int:
    """Read one bounded integer setting without accepting bool values.

    ``bool`` is a subclass of ``int`` in Python, so ``isinstance(True, int)``
    passes and ``max_pages: true`` would silently configure one page. It is
    rejected explicitly.
    """
    value = config.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise error(f"{key} must be an integer")
    if not minimum <= value <= maximum:
        raise error(f"{key} must be between {minimum} and {maximum}")
    return value


def bounded_float(
    config: dict[str, Any],
    key: str,
    default: float,
    *,
    minimum: float,
    maximum: float,
    error: type[ValueError],
) -> float:
    """Read one bounded float setting without accepting bool values.

    An ``int`` is accepted and widened — YAML's ``30`` and ``30.0`` are the
    same intent — but ``bool`` is rejected for the reason above.
    """
    value = config.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise error(f"{key} must be a number")
    numeric = float(value)
    if not minimum <= numeric <= maximum:
        raise error(f"{key} must be between {minimum} and {maximum}")
    return numeric


def bounded_bool(
    config: dict[str, Any],
    key: str,
    default: bool,
    *,
    error: type[ValueError],
) -> bool:
    """Read one boolean setting, refusing the truthy strings YAML allows."""
    value = config.get(key, default)
    if not isinstance(value, bool):
        raise error(f"{key} must be a boolean")
    return value
